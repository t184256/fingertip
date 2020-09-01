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
and imposing as few limits as possible.
If you look at it and think that it does nothing in the laziest way possible,
that's it.

It's currently in alpha stage.

# Teaser

Some examples of executing it from your shell:

``` bash
$ fingertip os.fedora + ssh  # install Fedora and SSH into it
$ fingertip os.alpine + console  # install Alpine, access serial console
$ fingertip os.alpine + ansible package --state=present --name=patch + ssh
$ fingertip backend.podman-criu + os.alpine + console  # containers!
$ fingertip os.fedora + script.debug myscript.sh  # checkpoint-powered debugger
```

An example of Python usage and writing your own steps:

``` python
import fingertip

def main(m=None, alias='itself'):
    m = m or fingertip.build('os.fedora')
    m = m.apply('ansible', 'lineinfile', path='/etc/hosts',
                line=f'127.0.0.1 {alias}')
    with m:
        assert '1 received' in m(f'ping -c1 {alias}').out
    return m
```

Put in `fingertip/plugins/demo.py`,
this can be now be used in pipelines:
```
$ fingertip demo
$ fingertip os.fedora + demo me
$ fingertip os.alpine + demo --alias=myself + ssh
```

## Installation

Refer to [INSTALL.md](INSTALL.md).


### Shell usage

If you have installed fingertip, invoke it as `fingertip`.

If you're running from a checkout, use `python3 <path to checkout>` instead
or make an alias.

If you're using a containerized version, invoke `fingertip-containerized`
(and hope for the best).

So,

``` bash
$ fingertip os.fedora + ssh
```

You should observe Fedora installation starting up,
then shutting down, booting up again and, finally,
giving you interactive control over the machine over SSH.

Invoke the same command again, and it should do nearly nothing, as
the downloads and the installation are already cached
in `~/.cache/fingertip`.
Enjoy fresh clean VMs brought up in mere seconds.
Feel like they're already at your fingertips.
Control them from console or from Python.


## Python usage

Let's see how manipulating machines can look like
(`fingertip/plugins/self_test/greeting.py`):

``` python
def make_greeting(m, greeting='Hello!'):                      # take a machine
    with m:                                                   # start if needed
        m.console.sendline(f"echo '{greeting}' > .greeting")  # type a command
        m.console.expect_exact(m.prompt)                      # wait for prompt
    return m                                                  # cache result


@fingertip.transient                                          # don't lock/save
def main(m, greeting='Hello!'):                               # take a machine
    m = m.apply(make_greeting, greeting=greeting)             # use cached step
    with m:                                                   # start if needed
        assert m('cat .greeting').out.strip() == greeting     # execute command
                                                              # do not save
```


Plugins are regular Python functions, nothing fancy.
You can just pass them `fingertip.build('fedora')` and that'll work.
Even this `@fingertip.transient` thing
is just an optimization hint to `.apply()`.

Here's what can happen inside such a function:

* It accepts a machine as the first argument
  (which may be already spun up or not, you don't know).
* It inspects it and applies more functions if it wants to,
  (extra steps applied through `.apply` are cached / reused if it's possible).
* Should any custom steps or changes be applied,
  the machine must be first spun up using a `with` block (`with m as m`).
  All modifications to the machine must happen inside that block,
  or risk being silently undone!
* Return the machine if the result should be cached and used for the next steps.
  Not returning one can and usually will undo all the changes you've made.
  If you don't intend to save the result, don't return m;
  additionally, decorate the function with `@fingertip.transient`
  so that fingertip can apply performance optimizations and avoid locking.
  There's much more to it, see `docs/on_transiency.md` for details.

The first function in the chain (or the one used in `fingertip.build`)
will not get a machine as the first argument.
To write a universal function, just use:
``` python
def func(m=None):
    m = m or fingertip.build('fedora')
    ...
```


## Disclaimer

Due to what exactly I cache and the early stage of development,
empty your `~/.cache/fingertip/machines` often, at least after each update.

``` bash
$ fingertip cleanup machines all
```

Some days the whole `~/.cache/fingertip` has to go.

``` bash
$ fingertip cleanup everything
```
