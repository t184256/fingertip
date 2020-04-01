%global git_date 20200408
%global git_commit 36dd03af8a120c692b95627b51b7951c629f3220
%{?git_commit:%global git_commit_hash %(c=%{git_commit}; echo ${c:0:7})}
%global rpm_release 1

Name:		fingertip
Version:	0.%{git_date}
Release:	%{rpm_release}.git%{git_commit_hash}%{?dist}
Summary:	Control VMs, containers and other machines with Python, leverage live snapshots

License:	GPLv3+
URL:		https://github.com/t184256/fingertip
Source0:	https://github.com/t184256/fingertip/archive/%{git_commit}/%{name}-%{git_commit_hash}.tar.gz

BuildArch:	noarch
BuildRequires:	python3
BuildRequires:	python3-devel

Requires:	qemu-system-x86
Requires:	ansible
Requires:	git-core
Requires:	python3
Requires:	python3-colorlog
Requires:	python3-paramiko
Requires:	python3-pexpect
Requires:	python3-pyxdg
Requires:	python3-CacheControl
Requires:	python3-requests
Requires:	python3-requests-mock
Requires:	python3-fasteners
Requires:	python3-lockfile
Requires:	python3-cloudpickle
Requires:	python3-GitPython
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
%setup -q -n fingertip-%{git_commit}

%build
# noop

%install
install -d -m 755 %{buildroot}%{_bindir}
install -d -m 755 %{buildroot}%{python3_sitelib}/fingertip

ln -s %{python3_sitelib}/fingertip/__main__.py %{buildroot}%{_bindir}/fingertip
cp -p -r fingertip %{buildroot}%{python3_sitelib}/fingertip
cp -p -r ssh_key %{buildroot}%{python3_sitelib}/fingertip
cp -p -r kickstart_templates %{buildroot}%{python3_sitelib}/fingertip
cp -p __main__.py %{buildroot}%{python3_sitelib}/fingertip


%files
%license COPYING
%doc README.md
%{_bindir}/fingertip
%dir %{python3_sitelib}/fingertip/
%{python3_sitelib}/fingertip/__main__.py
%{python3_sitelib}/fingertip/__pycache__/__main__.*
%{python3_sitelib}/fingertip/fingertip/*
%{python3_sitelib}/fingertip/ssh_key/*
%{python3_sitelib}/fingertip/kickstart_templates/*


%changelog
* Wed Apr 08 2020 Jakub Jelen <jjelen@redhat.com> - 0.20200408-1.git36dd03a
- Initial release
