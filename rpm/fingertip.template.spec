Name:		fingertip
Version:	{{ver}}
Release:        {{rel}}%{?dist}
Summary:	Control VMs, containers and other machines with Python, leverage live snapshots

License:	GPLv3+
URL:		https://github.com/t184256/fingertip
Source0:	https://github.com/t184256/fingertip/archive/{{git_commit}}/{{tarball}}

BuildArch:	noarch
BuildRequires:	python3
BuildRequires:	python3-devel

Requires:	ansible
Requires:	git-core
Requires:	openssh-clients
Requires:	python3
Requires:	python3-CacheControl
Requires:	python3-GitPython
Requires:	python3-cloudpickle
Requires:	python3-colorlog
Requires:	python3-fasteners
Requires:	python3-lockfile
Requires:	python3-paramiko
Requires:	python3-pexpect
Requires:	python3-pyxdg
Requires:	python3-requests
Requires:	python3-requests-mock
Requires:	qemu-system-x86
Requires:	qemu-img
Requires:	util-linux
Requires:	xfsprogs
Recommends:	podman

%description
This program/library aims to be a way to:

 * fire up VMs and containers in mere seconds using live VM snapshots
 * uniformly control machines from Python by writing small and neat functions
   transforming them
 * compose and transparently cache the results of these functions
 * build cool apps that exploit live VM snapshots and checkpoints
 * control other types of machines that are not local VMs

All while striving to be intentionally underengineered and imposing as little
limits as possible. If you look at it and think that it does nothing in
the laziest way possible, that's it.

%prep
%setup -q -n fingertip-{{git_commit}}

%build
# noop

%install
install -d -m 755 %{buildroot}%{_bindir}
install -d -m 755 %{buildroot}%{python3_sitelib}

ln -s %{python3_sitelib}/fingertip/main.py %{buildroot}%{_bindir}/fingertip
chmod +x %{buildroot}%{python3_sitelib}/fingertip/main.py
cp -p -r fingertip %{buildroot}%{python3_sitelib}/
cp -p -r ssh_key %{buildroot}%{python3_sitelib}/
cp -p -r kickstart_templates %{buildroot}%{python3_sitelib}/


%files
%license COPYING
%doc README.md
%{_bindir}/fingertip
%dir %{python3_sitelib}/fingertip/
%{python3_sitelib}/fingertip/*
%{python3_sitelib}/ssh_key/*
%{python3_sitelib}/kickstart_templates/*


# templated from build.sh
%changelog
