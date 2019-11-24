# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import json
import os
import sys
import time
import socket
import stat
import subprocess

import pexpect

import fingertip.machine
import fingertip.util.http_cache
from fingertip.util import free_port, log, path, reflink


CACHE_INTERNAL_IP, CACHE_INTERNAL_PORT = '10.0.2.244', 8080
CACHE_INTERNAL_URL = f'http://{CACHE_INTERNAL_IP}:{CACHE_INTERNAL_PORT}'
# TODO: add a way to customize smp
QEMU_COMMON_ARGS = ['-enable-kvm', '-cpu', 'host', '-smp', '4', '-nographic']


def create_image(path, size):
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', path, size],
                   check=True)


def main(arch='x86_64', ram_size='1G', disk_size='20G',
         custom_args=[], guest_forwards=[]):
    assert arch == 'x86_64'
    # FIXME: -tmp
    m = fingertip.machine.Machine()
    m.arch = arch
    m.qemu = QEMUFeatures(m, ram_size, disk_size, custom_args)
    return m


class QEMUFeatures:
    def __init__(self, vm, ram_size, disk_size, custom_args):
        self.vm = vm
        self.live = False
        self.ram_size, self.disk_size = ram_size, disk_size
        self.custom_args = custom_args
        self._image_to_clone = None
        create_image(os.path.join(vm.path, 'image.qcow2'), disk_size)

        vm.hook(load=self._load,
                up=self._up,
                down=self._down,
                save=self._save,
                disrupt=self._disrupt,
                clone=self._clone)
        self._load()

    def run(self, load='tip', guest_forwards=[], extra_args=[]):
        run_args = ['-loadvm', load] if load else []

        self.monitor = Monitor(self.vm)
        run_args += ['-qmp', (f'tcp:127.0.0.1:{self.monitor.port},'
                              'server,nowait,nodelay')]

        # TODO: extract SSH into a separate plugin?
        self.vm.ssh = SSH(key=path.fingertip('ssh_key', 'fingertip.paramiko'))
        ssh_host_forward = f'hostfwd=tcp:127.0.0.1:{self.vm.ssh.port}-:22'
        cache_guest_forward = (CACHE_INTERNAL_IP, CACHE_INTERNAL_PORT,
                               f'nc 127.0.0.1 {self.vm.http_cache.port}')
        guest_forwards = guest_forwards + [cache_guest_forward]
        run_args += ['-device', 'virtio-net,netdev=net0', '-netdev',
                     ','.join(['user', 'id=net0', ssh_host_forward] +
                              (['restrict=yes'] if self.vm.sealed else []) +
                              [f'guestfwd=tcp:{ip}:{port}-cmd:{cmd}'
                               for ip, port, cmd in guest_forwards])]

        if self._image_to_clone:
            reflink.auto(self._image_to_clone,
                         os.path.join(self.vm.path, 'image.qcow2'))
            self._image_to_clone = None
        image = os.path.join(self.vm.path, 'image.qcow2')
        run_args += ['-drive', f'file={image},cache=unsafe,if=virtio']

        run_args += ['-m', self.ram_size]

        args = QEMU_COMMON_ARGS + self.custom_args + run_args + extra_args
        self.vm.console = pexpect.spawn(f'qemu-system-{self.vm.arch}', args,
                                        echo=False, timeout=None,
                                        encoding='utf-8', logfile=sys.stdout)
        self.live = True

    def _load(self):
        log.debug('load_vm')

        self.vm.http_cache = fingertip.util.http_cache.HTTPCache()
        self.vm.http_cache.internal_ip = CACHE_INTERNAL_IP
        self.vm.http_cache.internal_port = CACHE_INTERNAL_PORT
        self.vm.http_cache.internal_url = CACHE_INTERNAL_URL

    def _up(self):
        if self.live:
            self.run()

    def _down(self):
        if self.live:
            log.debug(f'SAVE_LIVE {self.monitor}, {self.monitor._sock}')
            self.monitor.pause()
            self.monitor.checkpoint()
            self.monitor.commit()
            self.monitor.quit()
            self._go_down()

    def _save(self):
        self.vm.http_cache = None

    def _clone(self, parent, to_path):
        self._image_to_clone = os.path.join(parent.path, 'image.qcow2')

    def _disrupt(self):
        if self.live:
            self.vm.ssh.invalidate()

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

    def compress_image(self):
        assert not self.live
        image = os.path.join(self.vm.path, 'image.qcow2')
        subprocess.run(['qemu-img', 'convert', '-c', '-O', 'qcow2',
                        image, image + '-tmp'], check=True)
        os.rename(image + '-tmp', image)


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
        log.debug(f'monitor port {self.port}')
        self._sock = None

    def _connect(self, retries=10, timeout=1/32):
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            while retries:
                try:
                    self._sock.connect(('127.0.0.1', self.port))
                    break
                except ConnectionRefusedError:
                    retries -= 1
                    if not retries:
                        raise
                    time.sleep(timeout)
                    timeout *= 2
            log.debug(f'negotiation')
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

    def _execute(self, cmd, retries=10, timeout=1/32, **kwargs):
        self._connect(retries, timeout)
        log.debug(f'executing: {cmd} {kwargs}')
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
            log.error(r)
            raise UnknownVMException(r)
        return r

    def _expect(self, what=None):
        reply = self._recv()
        log.debug(f'expecting: {what}')
        log.debug(f'received: {reply}')
        if what is not None:
            assert reply == what
        return reply

    def resume(self):
        self._execute('cont')
        r = self._expect(None)
        assert set(r.keys()) == {'timestamp', 'event'}
        assert r['event'] == 'RESUME'
        self._expect({'return': {}})

    def pause(self):
        self._execute('stop')
        r = self._expect(None)
        assert set(r.keys()) == {'timestamp', 'event'}
        assert r['event'] == 'STOP'
        self._expect({'return': {}})
        self.vm._exec_hooks('disrupt')

    def quit(self):
        self.vm._exec_hooks('disrupt')
        self._execute('quit')
        self._expect({'return': {}})
        self._disconnect()

    def checkpoint(self, name='tip'):
        self._execute_human_command(f'savevm {name}')

    def restore(self, name='tip'):
        self._execute_human_command(f'loadvm {name}')

    def del_checkpoint(self, name):
        self._execute_human_command(f'delvm {name}')

    def commit(self):
        # saves the changes to current file only,
        # as live QEMU images are not CoW-backed with qcow2 mechanism,
        # just deduplicated with FS-level reflinks that QEMU is unaware of
        self._execute_human_command('commit all')


class SSH:
    def __init__(self, key, host='127.0.0.1', port=None):
        self.host, self.key = host, key
        self.port = port or free_port.find()
        log.debug(f'ssh port {self.port}')
        self._transport = None

    def connect(self, retries=10, timeout=1/32):
        import paramiko  # ... in parallel with VM spin-up
        if self._transport is None:
            pkey = paramiko.ECDSAKey.from_private_key_file(self.key)
            while True:
                try:
                    transport = paramiko.Transport((self.host, self.port))
                    break
                except paramiko.ssh_exception.SSHException as ex:
                    retries -= 1
                    if not retries:
                        raise
                    log.debug(f'timeout {timeout}, {ex}, {retries}')
                    time.sleep(timeout)
                    timeout *= 2
            transport.start_client()
            transport.auth_publickey('root', pkey)
            self._transport = transport
            log.debug(f'{self._transport}')

    def invalidate(self):
        self._transport = None

    def __call__(self, cmd, nocheck=False, get_pty=False):
        # TODO: get_pty unseals
        self.connect()
        channel = self._transport.open_session()
        if get_pty:
            channel.get_pty(term=os.getenv('TERM'))
        channel.set_combine_stderr(True)
        log.info(f'ssh command: {cmd}')
        channel.exec_command(cmd)
        outerr = b''
        while True:
            r = channel.recv(1)
            if not r:
                break
            sys.stdout.buffer.write(r)
            sys.stdout.flush()
            outerr += r
        retval = channel.recv_exit_status()
        log.info(f'ssh retval: {retval}')
        if nocheck:
            return retval, outerr.decode()
        assert(retval == 0)
        return outerr.decode()

    def upload(self, src, dst=None):  # may be unused
        import paramiko  # ... in parallel with VM spin-up
        assert os.path.isfile(src)
        dst = dst or os.path.basename(src)
        self.connect()
        sftp_client = paramiko.SFTPClient.from_transport(self._transport)
        sftp_client.put(src, dst)
        sftp_client.chmod(dst, os.stat(src).st_mode)

    @property
    def key_file(self):
        key_file = path.fingertip('ssh_key', 'fingertip')
        mode = os.stat(key_file)[stat.ST_MODE]
        if mode & 0o77:
            log.debug(f'fixing up permissions on {key_file}')
            os.chmod(key_file, mode & 0o7700)
        return key_file
