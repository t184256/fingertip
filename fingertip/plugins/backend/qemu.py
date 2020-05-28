# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import atexit
import json
import logging
import os
import selectors
import socket
import stat
import subprocess
import time

import fasteners
import pexpect

import fingertip.machine
import fingertip.util.http_cache
from fingertip.util import free_port, log, path, reflink, repeatedly, temp


SNAPSHOT_BASE_NAME = 'tip'  # it has to have some name
CACHE_INTERNAL_IP, CACHE_INTERNAL_PORT = '10.0.2.244', 8080
CACHE_INTERNAL_URL = f'http://{CACHE_INTERNAL_IP}:{CACHE_INTERNAL_PORT}'
# TODO: add a way to customize smp
QEMU_COMMON_ARGS = ['-enable-kvm', '-cpu', 'host,-vmx', '-smp', '4',
                    '-virtfs', f'local,id=shared9p,path={path.SHARED},'
                               'security_model=mapped-file,mount_tag=shared',
                    '-nographic',
                    '-object', 'rng-random,id=rng0,filename=/dev/urandom',
                    '-device', 'virtio-rng-pci,rng=rng0']


def main(arch='x86_64', ram_size='1G', disk_size='20G',
         custom_args=[], guest_forwards=[]):
    assert arch == 'x86_64'
    # FIXME: -tmp
    m = fingertip.machine.Machine('qemu')
    m.arch = arch
    m.qemu = QEMUNamespacedFeatures(m, ram_size, disk_size, custom_args)
    m.snapshot = SnapshotNamespacedFeatures(m)
    m._backend_mode = 'pexpect'

    def load():
        m.http_cache = fingertip.util.http_cache.HTTPCache()
        m.http_cache.internal_ip = CACHE_INTERNAL_IP
        m.http_cache.internal_port = CACHE_INTERNAL_PORT
        m.http_cache.internal_url = CACHE_INTERNAL_URL
    m.hooks.load.append(load)

    def up():
        if m.qemu.live:
            m.qemu.run()
            m.time_desync.fix_if_needed()
    m.hooks.up.append(up)

    def down():
        if m.qemu.live:
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

    m.breakpoint = lambda: m.apply('ssh', no_unseal=True)

    load()
    run = m.log.pipe_powered(subprocess.run,
                             stdout=logging.INFO, stderr=logging.ERROR)
    run(['qemu-img', 'create', '-f', 'qcow2',
         os.path.join(m.path, 'image.qcow2'), disk_size], check=True)
    return m


class QEMUNamespacedFeatures:
    def __init__(self, vm, ram_size, disk_size, custom_args):
        self.vm = vm
        self.live = False
        self.ram_size, self.disk_size = ram_size, disk_size
        self.custom_args = custom_args
        self._image_to_clone = None
        self._qemu = f'qemu-system-{self.vm.arch}'

    def run(self, load=SNAPSHOT_BASE_NAME, guest_forwards=[], extra_args=[]):
        if load:
            self.vm.time_desync.report(self.vm.time_desync.LARGE)
        run_args = ['-loadvm', load] if load else []

        self.monitor = Monitor(self.vm)
        run_args += ['-qmp', (f'tcp:127.0.0.1:{self.monitor.port},'
                              'server,nowait,nodelay')]

        # TODO: extract SSH into a separate plugin?
        self.vm.ssh = SSH(self.vm,
                          key=path.fingertip('ssh_key', 'fingertip.paramiko'))
        self.vm.shared_directory = SharedDirectory(self.vm)
        self.vm.exec = self.vm.ssh.exec
        ssh_host_forward = f'hostfwd=tcp:127.0.0.1:{self.vm.ssh.port}-:22'
        cache_guest_forward = (CACHE_INTERNAL_IP, CACHE_INTERNAL_PORT,
                               f'nc 127.0.0.1 {self.vm.http_cache.port}')
        guest_forwards = guest_forwards + [cache_guest_forward]
        run_args += ['-device', 'virtio-net,netdev=net0', '-netdev',
                     ','.join(['user', 'id=net0', ssh_host_forward] +
                              (['restrict=yes'] if self.vm.sealed else []) +
                              [f'guestfwd=tcp:{ip}:{port}-cmd:{cmd}'
                               for ip, port, cmd in guest_forwards])]

        image = os.path.join(self.vm.path, 'image.qcow2')
        if self._image_to_clone:
            required_space = os.path.getsize(self._image_to_clone) + 2**30
            lock = fasteners.process_lock.InterProcessLock('/tmp/.fingertip')
            lock.acquire()
            if self.vm._transient and temp.has_space(required_space):
                image = temp.disappearing_file('/tmp', hint='fingertip-qemu')
                reflink.auto(self._image_to_clone, image)
                lock.release()
            else:
                lock.release()
                reflink.auto(self._image_to_clone, image)
            self._image_to_clone = None
        run_args += ['-drive',
                     f'file={image},cache=unsafe,if=virtio,discard=unmap']

        run_args += ['-m', self.ram_size]

        os.makedirs(path.SHARED, exist_ok=True)

        args = QEMU_COMMON_ARGS + self.custom_args + run_args + extra_args
        self.vm.log.debug(' '.join(args))
        if self.vm._backend_mode == 'pexpect':
            pexp = self.vm.log.pseudofile_powered(pexpect.spawn,
                                                  logfile=logging.INFO)
            self.vm.console = pexp(self._qemu, args, echo=False,
                                   timeout=None,
                                   encoding='utf-8', codec_errors='ignore')
            self.live = True
        elif self.vm._backend_mode == 'direct':
            subprocess.run([self._qemu, '-serial', 'mon:stdio'] + args,
                           check=True)
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

    def compress_image(self):
        assert not self.live
        image = os.path.join(self.vm.path, 'image.qcow2')
        self.vm.log.info(f'compressing {image}')
        run = self.vm.log.pipe_powered(subprocess.run, stdout=logging.INFO,
                                       stderr=logging.ERROR)
        run(['qemu-img', 'convert', '-c', '-Oqcow2', image, image + '-tmp'],
            check=True)
        os.rename(image + '-tmp', image)


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

    def _connect(self, retries=12, timeout=1/32):
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            repeatedly.keep_trying(
                lambda: self._sock.connect(('127.0.0.1', self.port)),
                ConnectionRefusedError, retries=retries, timeout=timeout
            )
            self.vm.log.debug(f'negotiation')
            server_greeting = self._recv()
            assert 'QMP' in server_greeting
            self._execute('qmp_capabilities')
            self._expect({'return': {}})

    def _disconnect(self):
        self._sock.close()
        self._sock = None

    def _send(self, dictionary):
        self._sock.send(json.dumps(dictionary).encode())

    def _recv(self):
        r = b''
        while True:
            r += self._sock.recv(1)
            if not r or b'\n' in r:
                break
        return json.loads(r.decode())

    def _execute(self, cmd, retries=12, timeout=1/32, **kwargs):
        self._connect(retries, timeout)
        self.vm.log.debug(f'executing: {cmd} {kwargs}')
        if not kwargs:
            self._send({'execute': cmd})
        else:
            self._send({'execute': cmd, 'arguments': kwargs})

    def _execute_human_command(self, cmd):
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

    def _expect(self, what=None):
        reply = self._recv()
        self.vm.log.debug(f'expecting: {what}')
        self.vm.log.debug(f'received: {reply}')
        if what is not None:
            assert reply == what
        return reply

    def resume(self):
        self.vm.time_desync.report(self.vm.time_desync.SMALL)
        self._execute('cont')
        r = self._expect(None)
        assert set(r.keys()) == {'timestamp', 'event'}
        assert r['event'] == 'RESUME'
        self._expect({'return': {}})

    def pause(self):
        self.vm.hooks.disrupt()
        self.vm.time_desync.report(self.vm.time_desync.SMALL)
        self._execute('stop')
        r = self._expect(None)
        assert set(r.keys()) == {'timestamp', 'event'}
        assert r['event'] == 'STOP'
        self._expect({'return': {}})

    def quit(self):
        self.vm.hooks.disrupt()
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
    def __init__(self, m, key, host='127.0.0.1', port=None):
        self.host, self.key = host, key
        self.port = port or free_port.find()
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
        pkey = paramiko.ECDSAKey.from_private_key_file(self.key)

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
        transport.auth_publickey('root', pkey)
        self._transport = transport

    def invalidate(self):
        # gracefully terminate transport channel
        if self._transport:
            self.m.log.debug('Closing SSH session')
            self._transport.close()
        self._transport = None
        atexit.unregister(self.invalidate)

    def _stream_out_and_err(self, channel):
        sel = selectors.DefaultSelector()
        sel.register(channel, selectors.EVENT_READ)
        out, err, out_buf, err_buf = b'', b'', b'', b''
        last_out_time = time.time()
        silence_min = 0
        while True:
            sel.select(timeout=10)
            if channel.recv_ready():
                r = channel.recv(512)
                last_out_time = time.time()
                out += r
                out_buf += r
                out_lines = out_buf.split(b'\n')
                for out_line in out_lines[:-1]:
                    self.m.log.info(log.strip_control_sequences(out_line))
                out_buf = out_lines[-1]
            elif channel.recv_stderr_ready():
                r = channel.recv_stderr(512)
                last_out_time = time.time()
                err += r
                err_buf += r
                err_lines = err_buf.split(b'\n')
                for err_line in err_lines[:-1]:
                    self.m.log.info(log.strip_control_sequences(err_line))
                err_buf = err_lines[-1]
            elif channel.exit_status_ready():
                return out, err
            else:
                new_silence_min = int(time.time() - last_out_time) // 60
                if new_silence_min > silence_min:
                    silence_min = new_silence_min
                    self.m.log.debug(f'- no output for {silence_min} min -')

    def exec(self, *cmd, shell=False):
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
        key_file = path.fingertip('ssh_key', 'fingertip')
        s = os.stat(key_file)
        mode = s[stat.ST_MODE]
        owner = s[stat.ST_UID]
        # OpenSSH cares about permissions on key file only if the owner
        # matches current user
        if mode & 0o77 and owner == os.getuid():
            self.m.log.debug(f'fixing up permissions on {key_file}')
            os.chmod(key_file, mode & 0o7700)
        return key_file
