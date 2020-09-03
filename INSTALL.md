# Prerequisites

First and foremost, you need a recent Linux installation.
If you want to command VMs, you need KVM and
you really want a CoW-enabled filesystem or one-time root access to setup one.
If you want to command containers, you need podman `> 1.8`.


# Installation of a package

## Fedora COPR

If you want just to install and use it:

``` bash
$ sudo dnf copr enable asosedkin/fingertip
$ sudo dnf install fingertip
```

Invoke as `fingertip`.


## Running from a checkout

Check out this repository:
``` bash
$ git clone https://github.com/t184256/fingertip
```

(For NixOS / Nix users, there's a `shell.nix`, you know what to do.)


Install the required system dependencies (adjust accordingly):
``` bash
$ sudo <your package manager> install qemu ansible xfsprogs
```

### Running with system Python packages

To run fingertip with system Python packages, first install all required dependencies:

``` bash
$ sudo <your package manager> install python3-colorama python3-paramiko python3-pexpect python3-pyxdg python3-CacheControl python3-requests python3-requests-mock python3-fasteners python3-lockfile python3-cloudpickle python3-GitPython
```

Invoke as

``` bash
$ python3 <path-to-fingertip-checkout> os.fedora
```

### Running via Poetry

Poetry will install fingertip in an virtual environment and provide a way to execute it.

[Install poetry](https://python-poetry.org/docs/)
and install fingertip by running in project root:

``` bash
$ poetry install
```

Invoke ase

``` bash
$ poetry run fingertip os.fedora
```

### Last-resort: inside a container with Fedora

If you have an old system, but also Podman or Docker,
you can try out a containerized version
(`fingertip/fingertip-containerized`) that'll install
Fedora with all the required dependencies into a container.
This is not tested much, but it's reported to work.


# Initial configuration

First invocation of `fingertip` will execute the first setup wizard,
which will do its best to configure automatic cleanup and provide you
with a CoW-enabled filesystem.

## Cleanup

If you run fingertip from the checkout
and you don't have `fingertip` in your $PATH,
consider adding a cron job or something that'd invoke your equivalent of
`fingertip cleanup periodic` several times per day.
Otherwise you'll run out of disk space real soon.

## CoW

If you don't want your SSD to wear out prematurely,
you need a CoW-enabled filesystem on your `~/.cache/fingertip/machines`.
In practice, this probably means either `btrfs` or specially-created `xfs`
(see example below):

The interactive setup wizard will attempt to create one for you if needed.
In case you need to automate the setup, you can use the environment
variables `FINGERTIP_SETUP=auto|suggest|never` and `FINGERTIP_SETUP_SIZE`.
Finally, you can also perform the setup manually:

``` bash
$ mkdir -p ~/.cache/fingertip/
$ fallocate -l 25G ~/.cache/fingertip/cow.xfs.img
$ mkfs.xfs -m reflink=1 ~/.cache/fingertip/cow.xfs.img
$ sudo mount -o loop ~/.cache/fingertip/cow.xfs.img ~/.cache/fingertip/machines
$ sudo chown $USER ~/.cache/fingertip
```

(and, if you're going to export files later with httpd)

``` bash
$ sudo semanage fcontext -a -t user_home_dir_t ~/.cache/fingertip/(/.*)?
$ sudo restorecon -v ~/.cache/fingertip
```

(consider adding it to /etc/fstab so that it will get automounted on boot:)
``` bash
$ echo "$HOME/.cache/fingertip/cow.xfs.img $HOME/.cache/fingertip auto loop" | sudo tee -a /etc/fstab
```
