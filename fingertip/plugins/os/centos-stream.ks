repo --name=centos-stream --baseurl=http://mirror.centos.org/centos/8-stream/BaseOS/x86_64/os/
authselect --passalgo=sha512 --useshadow
autopart
bootloader --timeout=0
cmdline
keyboard us
lang en_US
rootpw fingertip
timezone UTC --utc

zerombr

firewall --disabled
network --hostname={HOSTNAME}
sshkey --username root '{SSH_PUBKEY}'

%packages --excludeWeakdeps --timeout 900
@Core --nodefaults
openssh-server
glibc-minimal-langpack
glibc-langpack-en
python3-libselinux
kexec-tools
-subscription-manager
-cronie
-sg3_*
-parted
-kernel-tools
-prefixdevname
-NetworkManager-team
-NetworkManager-tui
-firewalld
-irqbalance
-dracut-config-rescue
-lsscsi
-iprutils
-plymouth*
-glibc-all-langpacks
-langpacks-en
-iwl*-firmware
-microcode*
-tuned
-biosdevname
-lshw
-sssd-*
%end

%post --erroronfail
sed -i 's|\blocalhost\b|{HOSTNAME} localhost|' /etc/hosts
sed -i 's|^\[main\]$|[main]\nproxy={PROXY}\ndeltarpm=0\nzchunk=0\ninstall_weak_deps=0\ntimeout=900\nmax_parallel_downloads=16|' /etc/dnf/dnf.conf
sed -i 's|^baseurl=https://|baseurl=http://|' /etc/yum.repos.d/*
sed -i 's|^metalink=https://|metalink=http://|' /etc/yum.repos.d/*
sed -i 's|^metalink=\(.*\)$|metalink=\1\&protocol=http|' /etc/yum.repos.d/*
sed -i 's|gpgcheck=1|gpgcheck=0|' /etc/dnf/dnf.conf

# don't download metadata for debuginfo / source repositories
dnf -y config-manager --disable '*-debuginfo' '*-source'
dnf -y makecache
dnf -y remove linux-firmware
dnf -y update
dnf -y clean packages
fstrim -av
%end

poweroff
