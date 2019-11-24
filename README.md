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


## Preparations

Check out this repository:
``` bash
$ git clone https://github.com/t184256/fingertip
```

Install the dependencies (adjust accordingly):
``` bash
$ sudo <your package manager> install qemu python3-coloredlogs python3-paramiko python3-pexpect python3-xdg
```

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
$ python3 fingertip fedora + self_test.console_greeting
```

You should see Fedora installation starting up, then shutting down,
compressing an image, booting up again and, finally,
writing `'Hello!'` to file and outputting the contents of that file.

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


def main(m, greeting='Hello!'):                               # take a machine
    with m.apply(make_greeting, greeting=greeting) as m:      # modify, start
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
  Not returning one will undo all the changes (not available on some backends).

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
