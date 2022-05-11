{REPOS}

authselect --passalgo=sha512 --useshadow
autopart --type=plain
bootloader --timeout=0
cmdline  # or text?
keyboard us
lang en_US
rootpw fingertip
timesource --ntp-disable
timezone UTC --utc
zerombr

firewall --disabled
network --hostname={HOSTNAME}

sshkey --username root '{SSH_PUBKEY}'

%addon com_redhat_kdump --disable
%end

%packages --exclude-weakdeps --timeout 900
@Core --nodefaults
openssh-server
glibc-minimal-langpack
glibc-langpack-en
python3-libselinux
patch
-subscription-manager
-cronie
-sg3_*
-parted
-kexec-tools
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
sed -i 's|^\[main\]$|[main]\ndeltarpm=0\nzchunk=0\ninstall_weak_deps=0\ntimeout=900\nmax_parallel_downloads=16\nproxy={PROXY}|' /etc/dnf/dnf.conf
sed -i 's|^baseurl=https://|baseurl=http://|' /etc/yum.repos.d/*
sed -i 's|^metalink=https://|metalink=http://|' /etc/yum.repos.d/*
sed -i 's|^metalink=\(.*\)$|metalink=\1\&protocol=http|' /etc/yum.repos.d/*
sed -i 's|gpgcheck=1|gpgcheck=0|' /etc/dnf/dnf.conf
rm -f /etc/yum.repos.d/fedora-*

# don't download metadata for debuginfo / source repositories
dnf -y config-manager --disable '*-debuginfo' '*-source'
dnf -y makecache
dnf -y remove linux-firmware
dnf -y update
dnf -y clean packages
sed -i 's|proxy={PROXY}||' /etc/dnf/dnf.conf
fstrim -av
%end

poweroff
