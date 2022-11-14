Name:		fingertip
Version:	{{ver}}
Release:	{{rel}}%{?dist}
Summary:	Control VMs, containers and other machines with Python, leverage live snapshots

License:	GPLv3+
URL:		https://github.com/t184256/fingertip
Source0:	https://github.com/t184256/fingertip/archive/{{git_commit}}/{{tarball}}

BuildArch:	noarch
BuildRequires:	python3
BuildRequires:	python3-devel
BuildRequires:	systemd-rpm-macros

Requires:	ansible-core
Requires:	git-core
Requires:	nmap-ncat
Requires:	openssh-clients
Requires:	python3
Requires:	python3-CacheControl
Requires:	python3-GitPython
Requires:	python3-cloudpickle
Requires:	python3-colorama
Requires:	python3-fasteners
Requires:	python3-inotify_simple
Requires:	python3-lockfile
Requires:	python3-paramiko
Requires:	python3-pexpect
Requires:	python3-pyxdg
Requires:	python3-rangehttpserver
Requires:	python3-requests
Requires:	python3-requests-mock
Requires:	python3-ruamel-yaml
Requires:	qemu-system-x86-core
Requires:	qemu-img
Requires:	rsync
Requires:	util-linux
Requires:	xfsprogs
Recommends:	duperemove
Recommends:	createrepo_c
Recommends:	podman
Recommends:	ansible-collection-community-general  # ini_file

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

%global statedir %{_sharedstatedir}/fingertip

%install
install -d -m 755 %{buildroot}%{_bindir}
install -d -m 755 %{buildroot}%{python3_sitelib}

cp -p -r fingertip %{buildroot}%{python3_sitelib}/
cp -p -r ssh_key %{buildroot}%{python3_sitelib}/
cp -p -r kickstart_templates %{buildroot}%{python3_sitelib}/
cp -p __main__.py %{buildroot}%{python3_sitelib}/fingertip/
chmod +x %{buildroot}%{python3_sitelib}/fingertip/__main__.py
ln -s %{python3_sitelib}/fingertip/__main__.py %{buildroot}%{_bindir}/fingertip

install -d -m 755 %{buildroot}%{_unitdir}
install -m 644 rpm/fingertip-shared-cache.service %{buildroot}%{_unitdir}/

install -d -m 755 %{buildroot}%{_sbindir}
install -m 755 rpm/fingertip-shared-cache-demolish %{buildroot}%{_sbindir}/
install -m 755 rpm/fingertip-shared-cache-grow %{buildroot}%{_sbindir}/
install -m 755 rpm/fingertip-shared-cache-use %{buildroot}%{_sbindir}/

install -d -m 755 %{buildroot}%{_libexecdir}/fingertip
install -m 755 rpm/fingertip-shared-cache-setup %{buildroot}%{_libexecdir}/fingertip/shared-cache-setup

install -d -m 755 %{buildroot}%{statedir}
install -d -m 2755 %{buildroot}%{statedir}/shared_cache
install -d -m 2755 %{buildroot}%{statedir}/shared_cache/saviour

%files
%license COPYING
%doc README.md
%{_bindir}/fingertip
%dir %{python3_sitelib}/fingertip/
%{python3_sitelib}/fingertip/*
%{python3_sitelib}/ssh_key/*
%{python3_sitelib}/kickstart_templates/*


%package shared-cache
Summary:	Shared CoW-enabled cache for fingertip
Requires:	fingertip
Requires:	procps-ng
Requires:	systemd
Requires:	/usr/sbin/losetup
Requires:	/usr/sbin/semanage
Requires:	/usr/sbin/restorecon
%description shared-cache
Tools to set up a shared CoW-powered HTTP-exportable cache for fingertip.

After installing, you'll have to
`systemctl enable --now fingertip-shared-cache`,
`fingertip-shared-cache-grow $DESIRED_SIZE` the image
(if your FS didn't have CoW),
and, lastly, `fingertip-shared-cache-use $YOUR_USERNAME`.

%pre shared-cache
getent group fingertip >/dev/null || groupadd -r fingertip

%files shared-cache
%{statedir}
%attr(2775,root,fingertip) %{statedir}/shared_cache
%attr(2775,root,fingertip) %{statedir}/shared_cache/saviour
%{_libexecdir}/fingertip/shared-cache-setup
%{_unitdir}/fingertip-shared-cache.service
%{_sbindir}/fingertip-shared-cache-demolish
%{_sbindir}/fingertip-shared-cache-grow
%{_sbindir}/fingertip-shared-cache-use

%post shared-cache
if [[ $1 == 1 ]]; then
    chmod -R 2775 %{statedir}
    chgrp -R fingertip %{statedir}
    setfacl -dR --set u::rwx,g::rwx,o::- %{statedir}
    semanage fcontext -a -t httpd_sys_content_t "%{statedir}/shared_cache/saviour(/.*)?"
    restorecon -v %{statedir}/shared_cache
fi

# templated from build.sh
%changelog
