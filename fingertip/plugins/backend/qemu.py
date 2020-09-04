# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import atexit
import contextlib
import json
import logging
import os
import selectors
import socket
import stat
import subprocess
import threading
import time
import queue

import fasteners
import pexpect

import fingertip.machine
import fingertip.util.http_cache
from fingertip.util import free_port, log, path, reflink, repeatedly, temp
from fingertip.util import units


IGNORED_EVENTS = ('NIC_RX_FILTER_CHANGED', 'RTC_CHANGE', 'RESET')
SNAPSHOT_BASE_NAME = 'tip'  # it has to have some name
CACHE_INTERNAL_IP, CACHE_INTERNAL_PORT = '10.0.2.244', 8080
CACHE_INTERNAL_URL = f'http://{CACHE_INTERNAL_IP}:{CACHE_INTERNAL_PORT}'
# TODO: add a way to customize smp
QEMU_COMMON_ARGS = ['-enable-kvm', '-cpu', 'host', '-smp', '4',
                    '-virtfs', f'local,id=shared9p,path={path.SHARED},'
                               'security_model=mapped-file,mount_tag=shared',
                    '-nographic',
                    '-object', 'rng-random,id=rng0,filename=/dev/urandom',
                    '-device', 'virtio-balloon',
                    '-device', 'virtio-rng-pci,rng=rng0']


def main(arch='x86_64', ram_min='1G', ram_size='1G', ram_max='4G',
         disk_size='20G', custom_args=[], guest_forwards=[]):
    assert arch == 'x86_64'
    m = fingertip.machine.Machine('qemu')
    m.arch = arch
    m._host_forwards = []
    m.ram = RAMNamespacedFeatures(m, ram_min, ram_size, ram_max)
    m.qemu = QEMUNamespacedFeatures(m, disk_size, custom_args)
    m.snapshot = SnapshotNamespacedFeatures(m)
    m._backend_mode = 'pexpect'

    # TODO: extract SSH into a separate plugin?
    m.ssh = SSH(m)

    def load():
        m.http_cache = fingertip.util.http_cache.HTTPCache()
        m.http_cache.internal_ip = CACHE_INTERNAL_IP
        m.http_cache.internal_port = CACHE_INTERNAL_PORT
        m.http_cache.internal_url = CACHE_INTERNAL_URL

        if hasattr(m.ram, '_pre_down_size'):
            m.ram._target = m.ram._pre_down_size
            del m.ram._pre_down_size
    m.hooks.load.append(load)

    def up():
        if m.qemu.live:
            m.qemu.run()
            m.time_desync.fix_if_needed()
    m.hooks.up.append(up)

    def down():
        if m.qemu.live:
            if m.ram.min != m.ram.actual:
                m.log.info('driving RAM down to '
                           f'({units.binary(m.ram.min)})')
                m.ram._pre_down_size = m.ram._target
                m.ram.size = m.ram.min  # blocking
            m.log.debug(f'save_live {m.qemu.monitor}, {m.qemu.monitor._sock}')
            m.qemu.monitor.pause()
            m.qemu.monitor.checkpoint()
            m.qemu.monitor.commit()
            m.qemu.monitor.quit()
            m.qemu._go_down()
    m.hooks.down.append(down)

    def drop():
        if m.qemu.live:
            m.log.debug(f'drop {m.qemu.monitor}, {m.qemu.monitor._sock}')
            m.qemu.monitor.quit()
            m.qemu._go_down()
        if m._transient and m.qemu.image and os.path.exists(m.qemu.image):
            os.unlink(m.qemu.image)
    m.hooks.drop.append(drop)

    def save():
        m.http_cache = None
    m.hooks.save.append(save)

    def clone(to_path):
        m.qemu._image_to_clone = os.path.join(m.path, 'image.qcow2')
    m.hooks.clone.append(clone)

    def disrupt():
        if m.qemu.live:
            m.ssh.invalidate()
    m.hooks.disrupt.append(disrupt)

    def forward_host_port(hostport, guestport):
        if m.qemu.live:
            m.qemu.monitor.forward_host_port(hostport, guestport)
        m._host_forwards.append((hostport, guestport))
    m.forward_host_port = forward_host_port

    m.breakpoint = lambda: m.apply('ssh', unseal=False)

    load()
    run = m.log.pipe_powered(subprocess.run,
                             stdout=logging.INFO, stderr=logging.ERROR)
    run(['qemu-img', 'create', '-f', 'qcow2',
         os.path.join(m.path, 'image.qcow2'), disk_size], check=True)
    return m


class QEMUNamespacedFeatures:
    def __init__(self, vm, disk_size, custom_args):
        self.vm = vm
        self.live = False
        self.disk_size = disk_size
        self.custom_args = custom_args
        self.image, self._image_to_clone = None, None
        self.virtio_scsi = False  # flip before OS install for TRIM on Linux<5
        self._qemu = f'qemu-system-{self.vm.arch}'

    def run(self, load=SNAPSHOT_BASE_NAME, guest_forwards=[], extra_args=[]):
        if load:
            self.vm.time_desync.report(self.vm.time_desync.LARGE)
        run_args = ['-loadvm', load] if load else []

        self.monitor = Monitor(self.vm)
        run_args += ['-qmp', (f'tcp:127.0.0.1:{self.monitor.port},'
                              'server,nowait,nodelay')]

        self.vm.ssh.port = free_port.find()
        self.vm.shared_directory = SharedDirectory(self.vm)
        self.vm.exec = self.vm.ssh.exec
        host_forwards = [(self.vm.ssh.port, 22)] + self.vm._host_forwards
        host_forwards = [f'hostfwd=tcp:127.0.0.1:{h}-:{g}'
                         for h, g in host_forwards]
        cache_guest_forward = (CACHE_INTERNAL_IP, CACHE_INTERNAL_PORT,
                               f'nc 127.0.0.1 {self.vm.http_cache.port}')
        guest_forwards = guest_forwards + [cache_guest_forward]
        run_args += ['-device', 'virtio-net,netdev=net0', '-netdev',
                     ','.join(['user', 'id=net0'] + host_forwards +
                              (['restrict=yes'] if self.vm.sealed else []) +
                              [f'guestfwd=tcp:{ip}:{port}-cmd:{cmd}'
                               for ip, port, cmd in guest_forwards])]

        self.image = os.path.join(self.vm.path, 'image.qcow2')
        if self._image_to_clone:
            # let's try to use /tmp (which is, hopefully, tmpfs) for transients
            # if it looks empty enough
            cloned_to_tmp = False
            required_space = os.path.getsize(self._image_to_clone) + 2 * 2**30
            if self.vm._transient:
                # Would be ideal to have it global (and multiuser-ok)
                tmp_free_lock = path.cache('.tmp-free-space-check-lock')
                with fasteners.process_lock.InterProcessLock(tmp_free_lock):
                    if temp.has_space(required_space, where='/tmp'):
                        self.image = temp.disappearing_file(
                            '/tmp', hint='fingertip-qemu'
                        )
                        self.vm.log.debug('copying image to /tmp...')
                        reflink.auto(self._image_to_clone, self.image)
                        self.vm.log.debug('copying image to tmpfs completed')
                        cloned_to_tmp = True
            if not cloned_to_tmp:
                reflink.auto(self._image_to_clone, self.image)
            self._image_to_clone = None
        if self.virtio_scsi:
            run_args += ['-device', 'virtio-scsi-pci',
                         '-device', 'scsi-hd,drive=hd',
                         '-drive', f'file={self.image},cache=unsafe,'
                                   'if=none,id=hd,discard=unmap']
        else:
            run_args += ['-drive', f'file={self.image},cache=unsafe,'
                                   'if=virtio,discard=unmap']

        run_args += ['-m', str(self.vm.ram.max // 2**20)]

        os.makedirs(path.SHARED, exist_ok=True)

        args = QEMU_COMMON_ARGS + self.custom_args + run_args + extra_args
        self.vm.log.debug(' '.join(args))
        if self.vm._backend_mode == 'pexpect':
            # start connecting/negotiating QMP, later starts auto-ballooning
            threading.Thread(target=self.monitor.connect, daemon=True).start()
            pexp = self.vm.log.pseudofile_powered(pexpect.spawn,
                                                  logfile=logging.INFO)
            self.vm.console = pexp(self._qemu, args, echo=False,
                                   timeout=None,
                                   encoding='utf-8', codec_errors='ignore')
            self.live = True
        elif self.vm._backend_mode == 'direct':
            subprocess.run([self._qemu, '-serial', 'mon:stdio'] + args,
                           check=True)
            # FIXME: autoballooning won't start w/o the monitor connection!
            self.live = False
            self._go_down()

    def wait(self):
        self.vm.console.expect(pexpect.EOF)
        self.vm.console.close()
        assert self.vm.console.exitstatus == 0, 'QEMU terminated with an error'
        self._go_down()
        self.live = False

    def _go_down(self):
        if self.live:
            self.vm.console = None
            self.vm.ssh.invalidate()
        del self.vm.exec
        del self.monitor


class SnapshotNamespacedFeatures:
    def __init__(self, vm):
        self.vm = vm
        self.base_name = SNAPSHOT_BASE_NAME
        self.list = [self.base_name]  # FIXME: not always true =/
        self._frozen = False

    def checkpoint(self, name=SNAPSHOT_BASE_NAME):
        self.vm.qemu.monitor.checkpoint(name)
        self.list.append(name)

    def freeze(self):
        self.vm.qemu.monitor.pause()
        self._frozen = True

    def revert(self, name=SNAPSHOT_BASE_NAME):
        self.vm.qemu.monitor.restore(name)
        if not self._frozen:
            self.vm.time_desync.fix_if_needed()

    def unfreeze(self):
        self.vm.qemu.monitor.resume()
        self._frozen = False
        self.vm.time_desync.fix_if_needed()

    def remove(self, name):
        self.vm.qemu.monitor.del_checkpoint(name)
        self.list.remove(name)

    def purge(self, keep_base=True):
        for name in self.list:
            if not (keep_base and name == self.base_name):
                self.vm.qemu.monitor.del_checkpoint(name)
        self.list = [self.base_name] if keep_base else []


class VMException(RuntimeError):
    pass


class UnknownVMException(VMException):
    pass


class NotEnoughSpaceForSnapshotException(VMException):
    pass


class Monitor:
    def __init__(self, vm, port=None):
        self.vm = vm
        self.port = port or free_port.find()
        self.vm.log.debug(f'monitor port {self.port}')
        self._sock = None
        self._queue = queue.Queue()
        self._ram_actual_changed = threading.Event()
        self._ram_target_changed = threading.Event()
        self._command_execution_lock = threading.RLock()

    def connect(self, retries=12, timeout=1/32):
        if self._sock is None:
            with self._command_execution_lock:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.vm.log.debug(f'QMP connecting...')
                repeatedly.keep_trying(
                    lambda: self._sock.connect(('127.0.0.1', self.port)),
                    ConnectionRefusedError, retries=retries, timeout=timeout
                )
                self._execute('qmp_capabilities')
                threading.Thread(target=self._recv_thread, daemon=True).start()
                server_greeting = self._expect()
                assert set(server_greeting.keys()) == {'QMP'}
                self._expect({'return': {}})  # from qmp_capabilities
                self.vm.log.debug(f'QMP is ready')
            threading.Thread(target=self._ballooning_thread,
                             daemon=True).start()

    def _recv_thread(self):
        while self._sock:
            r = self._recv()
            if not r:
                break

            if 'event' in r:
                if r['event'] in IGNORED_EVENTS:
                    self.vm.log.debug(f'ignoring: QMP message {r}')
                    continue
                elif r['event'] == 'BALLOON_CHANGE':
                    assert 'data' in r and 'actual' in r['data']
                    self.vm.ram._actual = r['data']['actual']
                    self.vm.log.debug('memory ballooned to '
                                      f'{units.binary(self.vm.ram._actual)}')
                    self._ram_actual_changed.set()
                    continue

            self._queue.put(r)

    def _ballooning_thread(self):
        while self._sock:
            if self.vm.ram._actual != self.vm.ram._target:
                self.balloon(self.vm.ram._target)
                time.sleep(2)
            else:
                self._ram_target_changed.wait()
                self._ram_target_changed.clear()

    def _expect(self, what=None):  # None = nothing exact, caller inspects it
        reply = self._queue.get()
        self.vm.log.debug(f'expected: {what}')
        self.vm.log.debug(f'received: {reply}')
        if what is not None:
            assert reply == what
        return reply

    def _disconnect(self):
        with self._command_execution_lock:
            if self._sock:
                self._sock.close()
                self._sock = None
                self.vm.log.debug('qemu monitor disconnected')

    def _send(self, dictionary):
        self._sock.send(json.dumps(dictionary).encode())

    def _recv(self):
        r = b''
        try:
            while self._sock:
                r += self._sock.recv(1)
                if not r:
                    self._disconnect()
                    return
                if r[-1] == ord('\n'):
                    return json.loads(r.decode())
        except OSError:
            self._disconnect()

    def _execute(self, cmd, retries=12, timeout=1/32, **kwargs):
        self.connect(retries, timeout)
        self.vm.log.debug(f'executing: {cmd} {kwargs}')
        if not kwargs:
            self._send({'execute': cmd})
        else:
            self._send({'execute': cmd, 'arguments': kwargs})

    def _execute_human_command(self, cmd):
        with self._command_execution_lock:
            self._execute(f'human-monitor-command', **{'command-line': cmd})
            r = self._expect(None)
            while 'timestamp' in r and 'event' in r:
                r = self._expect(None)  # ignore these for now
            assert set(r.keys()) == {'return'}
            assert isinstance(r['return'], str)
            r = r['return']
            if 'Error while writing VM state: No space left on device' in r:
                raise NotEnoughSpaceForSnapshotException(r)
            elif r:
                self.vm.log.error(r)
                raise UnknownVMException(r)
            return r

    def resume(self):
        self.vm.time_desync.report(self.vm.time_desync.SMALL)
        with self._command_execution_lock:
            self._execute('cont')
            r = self._expect(None)
            assert set(r.keys()) == {'timestamp', 'event'}
            assert r['event'] == 'RESUME'
            self._expect({'return': {}})

    def pause(self):
        self.vm.hooks.disrupt()
        self.vm.time_desync.report(self.vm.time_desync.SMALL)
        with self._command_execution_lock:
            self._execute('stop')
            r = self._expect(None)
            assert set(r.keys()) == {'timestamp', 'event'}
            assert r['event'] == 'STOP'
            self._expect({'return': {}})

    def quit(self):
        self.vm.hooks.disrupt()
        if self._command_execution_lock.acquire(blocking=False):
            self._execute('quit')
            self._expect({'return': {}})
            self._disconnect()

    def checkpoint(self, name=SNAPSHOT_BASE_NAME):
        self._execute_human_command(f'savevm {name}')

    def restore(self, name=SNAPSHOT_BASE_NAME):
        self.vm.time_desync.report(self.vm.time_desync.SMALL
                                   if name != SNAPSHOT_BASE_NAME else
                                   self.vm.time_desync.LARGE)
        self.vm.hooks.disrupt()
        self._execute_human_command(f'loadvm {name}')

    def del_checkpoint(self, name):
        self._execute_human_command(f'delvm {name}')

    def commit(self):
        # saves the changes to current file only,
        # as live QEMU images are not CoW-backed with qcow2 mechanism,
        # just deduplicated with FS-level reflinks that QEMU is unaware of
        self._execute_human_command('commit all')

    def forward_host_port(self, hostport, guestport):
        self._execute_human_command('hostfwd_add '
                                    f'tcp:127.0.0.1:{hostport}-:{guestport}')

    def balloon(self, target_size):
        target_size = units.parse_binary(target_size)
        if self.vm.ram._actual != target_size:
            with self._command_execution_lock:
                self.vm.log.debug('sending a request to balloon to '
                                  f'{units.binary(target_size)} from '
                                  f'{units.binary(self.vm.ram._actual)}')
                self._execute(f'balloon', **{'value': target_size})
                self._expect({'return': {}})
        else:
            self.vm.log.debug('no ballooning needed, '
                              f'already at {units.binary(target_size)}')


class RAMNamespacedFeatures:
    def __init__(self, m, min_, target, max_):
        self._m = m
        self._min = units.parse_binary(min_)
        self._size = units.parse_binary(target)
        self._target = self._size
        self._max = units.parse_binary(max_)
        self._actual = self._max
        self._safeguard = 0

    def set_size_async(self, new):
        new = units.parse_binary(new)
        if new < self._min:
            self._m.log.warning(f'cannot set ram.size to {units.binary(new)}, '
                                'clipping to ram.min '
                                f'({units.binary(self._min)})')
            new = self._min
        if new < self._safeguard:
            self._m.log.warning(f"won't set ram.size to {units.binary(new)}, "
                                'clipping to ram.safeguard '
                                f'({units.binary(self._safeguard)})')
            new = self._safeguard
        if new > self._max:
            self._m.log.warning(f'cannot set ram.size to {units.binary(new)}, '
                                'clipping to ram.max '
                                f'({units.binary(self._max)})')
            new = self._max
        self._target = new
        if self._m.qemu.live and self._actual != new:
            self._m.log.debug(f'going to balloon to {units.binary(new)} '
                              f'from {units.binary(self._m.ram._actual)}')
            self._m.qemu.monitor._ram_target_changed.set()

    def wait_for_ballooning(self):
        if self._actual != self._target:
            start_time = time.time()
            while self._actual != self._target:
                self._m.qemu.monitor._ram_actual_changed.wait()
                self._m.qemu.monitor._ram_actual_changed.clear()
            ballooning_duration = time.time() - start_time
            if ballooning_duration > 2.5:
                self._m.log.warning(f'ballooning took {ballooning_duration}s, '
                                    'increasing ram.min may help with delays')
            else:
                self._m.log.debug(f'ballooning took {ballooning_duration}s')
                self._m.qemu.monitor._ram_target_changed.set()
        else:
            self._m.log.debug(f'ballooning not needed')

    @property
    def min(self):
        return self._min

    @min.setter
    def min(self, new):
        new = units.parse_binary(new)
        if new < self._safeguard:
            self._m.log.warning(f"won't set ram.min to {units.binary(new)}, "
                                'clipping to ram.safeguard '
                                f'({units.binary(self._safeguard)})')
            new = self._safeguard
        if new > self._max:
            self._m.log.warning(f'cannot set ram.min to {units.binary(new)}, '
                                'clipping to ram.max '
                                f'({units.binary(self._max)})')
            new = self._max
        self._m.log.debug(f'setting ram.min to {units.binary(new)}')
        self._min = new
        if self._target < new:
            self._m.log.debug(f'bumping ram.size to {units.binary(new)} '
                              'along with ram.min')
            self.size = new

    @property
    def size(self):
        return self._target

    @size.setter
    def size(self, new):
        self.set_size_async(new)
        if self._m.qemu.live:
            self.wait_for_ballooning()  # sync if up, async best-effort if down

    @property
    def max(self):
        return self._max

    @max.setter
    def max(self, new):
        self._m.log.error('cannot change ram.max dynamically, '
                          'use `backend.qemu --ram-max=100500M + ...`')
        return

    @property
    def safeguard(self):
        return self._safeguard

    @safeguard.setter
    def safeguard(self, new):
        self._safeguard = units.parse_binary(new)

    @property
    def actual(self):
        return self._actual

    @contextlib.contextmanager  # with m, m.ram('+2G'): ...
    def __call__(self, size, wait=True, wait_post=True):
        if isinstance(size, str) and size and size[0] in '+-':
            new_size = self.size + units.parse_binary(size)
            log.warning(f'{new_size}')
        elif isinstance(size, str) and size and size.startswith('>='):
            new_size = max(self.size, units.parse_binary(size[2:]))
        else:
            new_size = units.parse_binary(size)

        old_size = self.size
        if wait:
            self.size = new_size
        else:
            self.set_size_async(new_size)
        yield
        if wait_post:
            self.size = old_size
        else:
            self.set_size_async(old_size)


class SharedDirectory:
    def __init__(self, m):
        self.m = m
        self.mount_count = 0

    def __enter__(self):
        if not self.mount_count:
            self.m('mkdir -p /shared && '
                   'mount -t 9p -o trans=virtio shared /shared '
                   '  -oversion=9p2000.L')
        self.mount_count += 1

    def __exit__(self, *_):
        self.mount_count -= 1
        if not self.mount_count:
            self.m('umount /shared')


class SSH:
    _key_file = path.fingertip('ssh_key', 'fingertip')
    key_file_paramiko = path.fingertip('ssh_key', 'fingertip.paramiko')
    pubkey_file = path.fingertip('ssh_key', 'fingertip.pub')

    def __init__(self, m, host='127.0.0.1', port=None):
        self.host, self.port = host, port
        self.m = m
        self.m.log.debug(f'ssh port {self.port}')
        self._transport = None

    def connect(self, force_reconnect=False, retries=12, timeout=1/32):
        atexit.register(self.invalidate)
        import paramiko  # ... in parallel with VM spin-up
        if not force_reconnect and self._transport is not None:
            self._transport.send_ignore()
            if self._transport.is_authenticated():
                return  # the transport is already OK
        self._transport = None
        self.m.log.debug('waiting for the VM to spin up and offer SSH...')
        pubkey = paramiko.ECDSAKey.from_private_key_file(SSH.key_file_paramiko)

        def connect():
            self.m.log.debug('Trying to connect ...')
            t = paramiko.Transport((self.host, self.port))
            t.start_client()
            return t

        transport = repeatedly.keep_trying(
            connect,
            paramiko.ssh_exception.SSHException,
            retries=retries, timeout=timeout
        )
        transport.auth_publickey('root', pubkey)
        self._transport = transport

    def invalidate(self):
        # gracefully terminate transport channel
        if self._transport:
            self.m.log.debug('Closing SSH session')
            self._transport.close()
        self._transport = None
        atexit.unregister(self.invalidate)

    def _stream_out_and_err(self, channel, quiet=False):
        m_log = self.m.log.info if not quiet else self.m.log.debug
        sel = selectors.DefaultSelector()
        sel.register(channel, selectors.EVENT_READ)
        out, err, out_buf, err_buf = b'', b'', b'', b''
        last_out_time = time.time()
        silence_min = 0
        linebreak = ord(b'\n')
        while True:
            sel.select(timeout=10)
            activity = False
            while channel.recv_ready():
                r = channel.recv(16384)
                out += r
                out_buf += r
                if linebreak in r:
                    out_lines = out_buf.split(b'\n')
                    for out_line in out_lines[:-1]:
                        m_log(log.strip_control_sequences(out_line))
                    out_buf = out_lines[-1]
                activity = True
            while channel.recv_stderr_ready():
                r = channel.recv_stderr(16384)
                err += r
                err_buf += r
                if linebreak in r:
                    err_lines = err_buf.split(b'\n')
                    for err_line in err_lines[:-1]:
                        m_log(log.strip_control_sequences(err_line))
                    err_buf = err_lines[-1]
                activity = True
            if activity:
                last_out_time = time.time()
            else:
                if channel.exit_status_ready():
                    return out, err
                new_silence_min = int(time.time() - last_out_time) // 60
                if new_silence_min > silence_min:
                    silence_min = new_silence_min
                    self.m.log.debug(f'- no output for {silence_min} min -')

    def exec(self, *cmd, shell=False, quiet=False):
        cmd = (' '.join(["'" + a.replace("'", r"\'") + "'" for a in cmd])
               if not shell else cmd[0])
        self.connect()
        try:
            channel = self._transport.open_session()
        except EOFError:
            self.m.log.warning('EOFError on SSH exec, retrying in 2 sec...')
            time.sleep(2)
            self.connect(force_reconnect=True)
            channel = self._transport.open_session()
        if quiet:
            self.m.log.debug(f'ssh command: {cmd}')
        else:
            self.m.log.info(f'ssh command: {cmd}')
        channel.exec_command(cmd)
        out, err = self._stream_out_and_err(channel)
        retval = channel.recv_exit_status()
        return fingertip.exec.ExecResult(retval, out, err)

    def upload(self, src, dst=None):  # may be unused
        import paramiko  # ... in parallel with VM spin-up
        assert os.path.isfile(src)
        dst = dst or os.path.basename(src)
        self.connect()
        sftp_client = paramiko.SFTPClient.from_transport(self._transport)
        sftp_client.put(src, dst)
        sftp_client.chmod(dst, os.stat(src).st_mode)

    def download(self, src, dst=None):
        import paramiko  # ... in parallel with VM spin-up
        dst = dst or os.path.basename(src)
        self.connect()
        sftp_client = paramiko.SFTPClient.from_transport(self._transport)
        sftp_client.stat(src)  # don't truncate dst if src does not exist
        sftp_client.get(src, dst)

    @property
    def key_file(self):
        s = os.stat(SSH._key_file)
        mode = s[stat.ST_MODE]
        owner = s[stat.ST_UID]
        # OpenSSH cares about permissions on key file only if the owner
        # matches current user
        if mode & 0o77 and owner == os.getuid():
            self.m.log.debug(f'fixing up permissions on {SSH._key_file}')
            os.chmod(SSH._key_file, mode & 0o7700)
        return SSH._key_file

    @property
    def pubkey(self):
        self.m.expiration.depend_on_a_file(SSH.pubkey_file)
        with open(SSH.pubkey_file) as f:
            return f.read().strip()
