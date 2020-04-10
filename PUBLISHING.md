# RPM packaging

The template in `fingertip.spec` contains template for packaging fingertip in
Fedora and RHEL. As fingertip does not provide any releases yet, the latest
git version is packaged (`git_commit` macro) and version is considered as
`0.git_date`, where `git_date` is the date of package creation.

# Content

Aside of the spec file, there are additional files. One of them is `fingertip.in`
wrapper, which is used to invoke `fingertip` command by the end users. It takes
care of correct location of the python library.

# Updating package

For pulling new changes from upstream, update `git_commit` and `git_date`
constants to latest release and revert `rpm_release` to 1. Then download
latest snapsthot from github (`GIT_SHORTCOMMIT` is first 7 characters
of `GIT_COMMIT`):

    wget https://github.com/t184256/fingertip/archive/{GIT_COMMIT}.tar.gz -O fingertip-{GIT_SHORTCOMMIT}.tar.gz

For bugfix release, update patches, fix spec file and bump `rpm_release` macro.

Insert a new changelog entry.

Try to build a package using your favorite tool from current directory, for example:

    fedpkg --release=master local

Submit updated spec file and package update to Fedora/copr.
