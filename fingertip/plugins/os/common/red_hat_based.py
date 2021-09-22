# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2021 Red Hat, Inc., see CONTRIBUTORS.

dnf_plugin_source = """
import os

import dnf


class ProxyAll(dnf.Plugin):
    name = "proxyall"
    def config(self):
        if not os.access('/etc/dnf/plugins/proxyall', os.F_OK):
            return
        self.base.conf.proxy = '$PROXY'
        for name, repo in self.base.repos.items():
            if repo.baseurl:
                repo.baseurl = [b.replace('https:', 'http:')
                                for b in repo.baseurl]
            if repo.metalink:
                repo.metalink = repo.metalink.replace('https:', 'http:')
                repo.metalink = repo.metalink + '&protocol=http'
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
        plugindir = m('find /usr/lib/py* -name dnf-plugins').out.strip()
        source = dnf_plugin_source.replace('$PROXY', m.http_cache.internal_url)
        m(f'cat > {plugindir}/proxyall.py <<EOF\n{source}EOF')
        m('touch /etc/dnf/plugins/proxyall')
        m._package_manager_proxied = True
        return m


# ---


YUM_PATCH = """
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
