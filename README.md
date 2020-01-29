`fingertip`
-----------

This program/library aims to be a way to:

* fire up VMs and containers in mere seconds using live VM snapshots
* uniformly control machines from Python by writing small and neat functions
  transforming them
* compose and transparently cache the results of these functions
* build cool apps that exploit live VM snapshots and checkpoints
* control other types of machines that are not local VMs

All while striving to be intentionally underengineered
and imposing as little limits as possible.
If you look at it and think that it does nothing in the laziest way possible,
that's it.

It's in an early demo stage.

Some examples of executing it:

``` bash
$ python3 fingertip os.fedora + ssh  # install Fedora and SSH into it
$ python3 fingertip os.alpine + console  # install Alpine, access serial console
$ python3 fingertip os.alpine + ansible package --state=present --name=patch
$ python3 fingertip backend.podman-criu + os.alpine + console  # containers!
$ python3 fingertip backend.podman-criu + os.alpine + exec 'free -m'
```

An example of Python usage and writing your own steps:

``` python
import fingertip

def main(m=None, alias='itself'):
    m = m or fingertip.build('os.fedora')
    m = m.apply('ansible', 'lineinfile', path='/etc/hosts',
                line='127.0.0.1 itself')
    with m:
        assert '1 received' in m('ping -c1 itself').out
    return m
```

Put in `fingertip/plugins/demo.py`, this can be used as
```
$ python3 fingertip demo
```


## Preparations

### Dependencies

Check out this repository:
``` bash
$ git clone https://github.com/t184256/fingertip
```

Install the dependencies (adjust accordingly):
``` bash
$ sudo <your package manager> install qemu ansible python3-colorlog python3-paramiko python3-pexpect python3-xdg python3-CacheControl python3-requests python3-requests-mock python3-fasteners python3-lockfile python3-cloudpickle
```

OR, if you have Podman or Docker, you can try out a containerized version
(`fingertip/fingertip-containerized`) that'll install
Fedora with all the required dependencies into a container.

### CoW

If you don't want your SSD to wear out prematurely,
you need a CoW-enabled filesystem on your `~/.cache/fingertip/machines`.
In practice, this probably means either `btrfs` or specially-created `xfs`
(see example below):

``` bash
$ mkdir -p ~/.cache/fingertip/machines
$ fallocate -l 20G ~/.cache/fingertip/machines/for-machines.xfs
$ mkfs.xfs -m reflink=1 ~/.cache/fingertip/machines/for-machines.xfs
$ sudo mount -o loop ~/.cache/fingertip/machines/for-machines.xfs ~/.cache/fingertip/machines
$ sudo chown $USER ~/.cache/fingertip/machines
```

(consider adding it to /etc/fstab so that you don't forget about it:)
``` bash
$ echo "$HOME/.cache/fingertip/for-machines.xfs $HOME/.cache/fingertip/machines auto loop
```


### The shell side of things

Now run `fingertip` with `python3 <path to checkout>`:

``` bash
$ python3 fingertip os.fedora + ssh
```

(or, if you are using a containerized version:)
``` bash
$ fingertip/fingertip-containerized os.fedora + ssh
```

You should observe Fedora installation starting up, then shutting down,
compressing an image, booting up again and, finally,
giving you interactive control over the machine over SSH.

Invoke the same command again, and it should do nearly nothing,
the downloads, the installation and half of the test are already cached
in `~/.cache/fingertip`.
Enjoy fresh clean VMs brought up in mere seconds.
Feel like they're already at your fingertips.
Control them from console or from Python.


## The Python side of things

Let's see how manipulating machines can look like
(`fingertip/plugins/self_test/console_greeting.py`):

``` python
def make_greeting(m, greeting='Hello!'):                      # take a machine
    with m:                                                   # start if needed
        m.console.sendline(f"echo '{greeting}' > .greeting")  # execute command
        m.console.expect_exact(m.prompt)                      # wait for prompt
        return m                                              # cache result


@fingertip.transient                                          # do not lock
def main(m, greeting='Hello!'):                               # take a machine
    m = m.apply(make_greeting, greeting=greeting):            # modify
    with m.transient():                                       # start
        m.console.sendline(f"cat .greeting")                  # execute command
        m.console.expect_exact(greeting)                      # get output
        m.console.expect_exact(m.prompt)                      # wait for prompt
                                                              # do not save
```

These are regular Python functions, nothing fancy.
You can just pass them `fingertip.build('fedora')` and that'll work.

Here's what can happen inside such a function:

* It accepts a machine as the first argument
  (which may be already spun up or not, you don't know).
* It inspects it and applies more functions if it wants to,
  (extra steps applied through `.apply` are cached if it's possible).
* Should any custom steps be applied, the machine must be first
  spun up using a `with` block (`with m as m`).
  All custom modifications of the machine must live inside that block!
* Return the machine if the result should be cached and used for the next steps.
  Not returning one will undo all the changes (not available on all backends).
  If you don't intend to save the result, also 1) decorate the function with
  `@fingertip.transient` and 2) use `.transient()` with `with`.

The first function in the chain (or the one used in `build`)
will not get a machine as the first argument.
To write a universal function, just use:
``` python
def func(m=None):
    m = m or fingertip.build('fedora')
    ...
```

NOTE: `m.apply` happening outside the `with` block will use a cached machine
with the test file already present if there is one.


## Disclaimer

Due to what exactly I cache and the early stage of development,
empty your `~/.cache/fingertip/machines` often, at least after each update.

``` bash
$ python3 fingertip cleanup machines all
```
