# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2021 Red Hat, Inc., see CONTRIBUTORS.

# DNF 5 (ELN, Fedora 41+?)


ACTION_TMPL = """
#!/usr/bin/bash
if [ ! -e /etc/dnf/plugins/proxyall ]; then exit 0; fi
# Override proxy on the fly if the file is present
echo conf.proxy={PROXY}
# Downgrade HTTPS to HTTP in repo configurations on-disk, see
# https://github.com/rpm-software-management/dnf5/issues/1345
sed -i 's|https://|http://|' /etc/yum.repos.d/*.repo
# Change metalinks to baseurls while we are at it
sed -i 's|^metalink|#metalink|' /etc/yum.repos.d/*.repo
sed -i 's|^#baseurl|baseurl|' /etc/yum.repos.d/*.repo
""".lstrip()


def proxy_dnf_action(m):
    if hasattr(m, '_package_manager_proxied') and m._package_manager_proxied:
        return m

    def disable_proxy():
        if m._package_manager_proxied:
            m._package_manager_proxied = False
            return m('rm -f /etc/dnf/plugins/proxyall')
    m.hooks.disable_proxy.append(disable_proxy)

    with m:
        action = ACTION_TMPL.format(PROXY=m.http_cache.internal_url)
        m(f'cat > /usr/sbin/_fingertip_dnfaction <<\\EOF\n' + action + 'EOF\n')
        m('''
            set -uexo pipefail
            chmod +x /usr/sbin/_fingertip_dnfaction
            echo post_base_setup::::/usr/sbin/_fingertip_dnfaction \
                    > /etc/dnf/libdnf5-plugins/actions.d/fingertip.actions
            touch /etc/dnf/plugins/proxyall
        ''')

        # Also downgrade copr to http. no opt-out. --hub must be used
        m('''
            set -uexo pipefail
            sed -i 's|protocol = https|protocol = http|g' \
                    /etc/dnf/plugins/copr.conf
            sed -i 's|port = 443|port = 80|g' /etc/dnf/plugins/copr.conf
        ''')
        m._package_manager_proxied = True
    return m


# DNF <4 (RHEL-8, RHEL-9, RHEL-10?, Fedora <41)


COPR_PATCH = r"""
diff --git a/plugins/copr.py b/plugins/copr.py
index 43aa896..faa56bd 100644
--- a/plugins/copr.py
+++ b/plugins/copr.py
@@ -196,6 +196,9 @@ class CoprCommand(dnf.cli.Command):
                 self.copr_hostname = copr_hub.split('://', 1)[1]
                 self.copr_url = copr_hub

+        if os.access('/etc/dnf/plugins/proxyall', os.F_OK):
+            self.copr_url = self.copr_url.replace('https://', 'http://')
+
     def _read_config_item(self, config, hub, section, default):
         try:
             return config.get(hub, section)
"""


DNF_PLUGIN_SOURCE = """
import os

import dnf


class ProxyAll(dnf.Plugin):
    name = "proxyall"
    def config(self):
        if not os.access('/etc/dnf/plugins/proxyall', os.F_OK):
            return
        self.base.conf.proxy = '$PROXY'
        for name, repo in self.base.repos.items():
            if any('://localhost' in url for url in repo.baseurl):
                repo.proxy = ''
            if repo.baseurl:
                repo.baseurl = [b.replace('https:', 'http:')
                                for b in repo.baseurl]
            if repo.metalink:
                repo.metalink = repo.metalink.replace('https:', 'http:')
                repo.metalink = repo.metalink + '&protocol=http'
            if repo.gpgkey:
                repo.gpgkey = [k.replace('https:', 'http:')
                               for k in repo.gpgkey]
"""


def proxy_dnf(m):
    if hasattr(m, '_package_manager_proxied') and m._package_manager_proxied:
        return m

    def disable_proxy():
        if m._package_manager_proxied:
            m._package_manager_proxied = False
            return m('rm -f /etc/dnf/plugins/proxyall')
    m.hooks.disable_proxy.append(disable_proxy)

    with m:
        source = COPR_PATCH.replace('$PROXY', m.http_cache.internal_url)
        copr_py_files = m('find /usr/lib/py*/*/dnf-plugins -name copr.py').out
        for copr_py_file in copr_py_files.strip().split():
            m(f'patch -p1 {copr_py_file} <<EOF\n{source}EOF',
              check=not hasattr(m, 'package_manager_proxied'))  # 1st time

        source = DNF_PLUGIN_SOURCE.replace('$PROXY', m.http_cache.internal_url)
        plugindirs = m('find /usr/lib/py* -name dnf-plugins').out
        for plugindir in plugindirs.strip().split():
            m(f'cat > {plugindir}/proxyall.py <<EOF\n{source}EOF')
            m('touch /etc/dnf/plugins/proxyall')
        m._package_manager_proxied = True
        return m


# YUM (RHEL-6, RHEL-7)


YUM_PATCH = r"""
diff --git a/yum/yumRepo.py b/yum/yumRepo.py
index 31b7c85..e449f7a 100644
--- a/yum/yumRepo.py
+++ b/yum/yumRepo.py
@@ -584,6 +584,9 @@ class YumRepository(Repository, config.RepoConf):
                 proto, rest = re.match('(\w+://)(.+)', proxy_string).groups()
                 proxy_string = '%s%s@%s' % (proto, auth, rest)

+        if os.access('/etc/yum/proxyall', os.F_OK):
+            proxy_string = '$PROXY'
+
         if proxy_string is not None:
             self._proxy_dict['http'] = proxy_string
             self._proxy_dict['https'] = proxy_string
@@ -832,6 +835,8 @@ class YumRepository(Repository, config.RepoConf):

         self.mirrorurls = self._replace_and_check_url(mirrorurls)
         self._urls = self.baseurl + self.mirrorurls
+        if os.access('/etc/yum/proxyall', os.F_OK):
+            self._urls = [b.replace('https:', 'http:') for b in self._urls]
         # if our mirrorlist is just screwed then make sure we unlink a mirrorlist cache
         if len(self._urls) < 1:
             if hasattr(self, 'mirrorlist_file') and os.path.exists(self.mirrorlist_file):
@@ -891,2 +897,5 @@ class YumRepository(Repository, config.RepoConf):
             if not self._metalinkCurrent():
+                if os.access('/etc/yum/proxyall', os.F_OK):
+                    self.metalink = self.metalink.replace('https:', 'http:')
+                    self.metalink += '&protocol=http'
                 url = misc.to_utf8(self.metalink)
"""

def proxy_yum(m):
    if hasattr(m, '_package_manager_proxied') and m._package_manager_proxied:
        return m

    def disable_proxy():
        if m._package_manager_proxied:
            m._package_manager_proxied = False
            return m('rm -f /etc/yum/proxyall')
    m.hooks.disable_proxy.append(disable_proxy)

    with m:
        c = YUM_PATCH.replace('$PROXY', m.http_cache.internal_url)
        m(f'patch -p1 /usr/lib/pyth*/site-packages/yum/yumRepo.py <<EOF{c}EOF')
        m('touch /etc/yum/proxyall')
        m._package_manager_proxied = True
        return m
