<?php
/**
 * Frappe Manager — Adminer login plugin. Static asset, identical for every
 * bench: bench-specific data (sites, DB credentials, redis hosts) is read
 * from /fm-sites at request time, so password changes take effect on the
 * next request with no rebuild needed. Renders one-click login cards per
 * site database and bench redis instance, followed by the stock Adminer
 * login form for manual connections.
 */
// ADMINER_PLUGINS cannot load driver plugins, so the redis driver (Adminer >= 5.4.4,
// pure PHP over sockets) is loaded via require_once below instead.
require_once('plugins/drivers/redis.php');
require_once('plugins/login-servers.php');

class FMLoginServers extends AdminerLoginServers {
    protected $fmCreds = array();
    protected $fmMeta = array();

    function __construct() {
        $servers = array();
        $creds = array();
        $meta = array();
        $common = json_decode((string) @file_get_contents('/fm-sites/common_site_config.json'), true) ?: array();
        // Site names become the `?server=` key in the login URL; Adminer rejects
        // keys with spaces or non-ASCII characters ("Invalid server." / HTTP 403).
        foreach (glob('/fm-sites/*/site_config.json') as $file) {
            $site = basename(dirname($file));
            $cfg = json_decode((string) file_get_contents($file), true) ?: array();
            // db_socket overrides db_host/db_port for Frappe, but this container cannot
            // reach a unix socket in another one; skip the card rather than guess a host.
            if (!empty($cfg['db_socket']) && empty($cfg['db_host'])) {
                continue;
            }
            $host = (string) ($cfg['db_host'] ?? $common['db_host'] ?? 'mariadb');
            $port = (int) ($cfg['db_port'] ?? $common['db_port'] ?? 0);
            // Adminer's host_port() only recognises a port after a plain host or a
            // bracketed [ipv6]; a bare IPv6 literal needs brackets before a port is appended.
            $endpoint = $host;
            if ($port && $port !== 3306 && !preg_match('~^(\[.+]|[^:]+):[^:]+$~', $host)) {
                $bare_ipv6 = strpos($host, ':') !== false && $host[0] !== '[';
                $endpoint = ($bare_ipv6 ? '[' . $host . ']' : $host) . ':' . $port;
            }
            $servers[$site] = array(
                'server' => $endpoint,
                'driver' => 'server',
            );
            $creds[$site] = array((string) ($cfg['db_name'] ?? ''), (string) ($cfg['db_password'] ?? ''));
            $sub = ($endpoint === $host) ? 'MariaDB · site database' : 'MariaDB · site database · ' . $endpoint;
            // db_ssl_ca pins TLS for this site's DB, but the CA file sits outside the
            // /fm-sites mount and this plugin has no connectSsl() override to apply it.
            if (!empty($cfg['db_ssl_ca'])) {
                $sub .= ' · TLS not applied by Adminer';
            }
            $meta[$site] = array('title' => $site, 'sub' => $sub, 'icon' => '🗄');
        }
        foreach (array('redis_cache' => array('redis-cache', 'Redis Cache', '⚡'), 'redis_queue' => array('redis-queue', 'Redis Queue', '📬')) as $key => $info) {
            if (empty($common[$key])) {
                continue;
            }
            $host = parse_url($common[$key], PHP_URL_HOST);
            $port = parse_url($common[$key], PHP_URL_PORT) ?: 6379;
            $servers[$info[0]] = array('server' => $host . ':' . $port, 'driver' => 'redis');
            $creds[$info[0]] = array('', '');
            $meta[$info[0]] = array('title' => $info[1], 'sub' => 'redis · ' . $host . ':' . $port, 'icon' => $info[2]);
        }
        $this->fmCreds = $creds;
        $this->fmMeta = $meta;
        // NOTE: intentionally NOT calling parent::__construct() — it unconditionally
        // overwrites auth[driver], which breaks manual logins to unlisted servers.
        $this->servers = $servers;
        // Card buttons post fm_server (their own field) so they never collide
        // with the stock auth[server] text input also present in the form.
        $fmKey = (string) ($_POST["fm_server"] ?? '');
        if ($fmKey !== '' && isset($this->servers[$fmKey])) {
            $_POST["auth"]["server"] = $fmKey;
            $_POST["auth"]["driver"] = $this->servers[$fmKey]["driver"];
            $_POST["auth"]["username"] = '';
            $_POST["auth"]["password"] = '';
        } elseif (isset($_POST["auth"]["server"]) && isset($this->servers[$_POST["auth"]["server"]])) {
            // `isset` on the nested key rather than a truth test on `$_POST["auth"]`: on a plain
            // GET there is no POST at all, and PHP 8 emits "Undefined array key" for that read on
            // every page load. Warnings land in the response body, ahead of Adminer's own output.
            $_POST["auth"]["driver"] = $this->servers[$_POST["auth"]["server"]]["driver"];
        }
    }

    function credentials() {
        $key = Adminer\SERVER;
        if (isset($this->servers[$key])) {
            $c = $this->fmCreds[$key];
            return array($this->servers[$key]['server'], $c[0], $c[1]);
        }
        return null; // unlisted server -> default Adminer behavior (manual login)
    }

    function login($login, $password) {
        if (isset($this->servers[Adminer\SERVER])) {
            return true; // one-click targets: skip password checks
        }
        return null; // manual logins: defer to default validation
    }

    function loginFormField($name, $heading, $value) {
        if ($name == 'driver') {
            $html = "<style>"
                . ".fm-cards{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 4px;}"
                . ".fm-card{display:flex;flex-direction:column;align-items:flex-start;gap:4px;cursor:pointer;"
                . "border:1px solid #d0d0d0;border-radius:10px;padding:14px 18px;min-width:190px;background:#fff;"
                . "font:inherit;text-align:left;transition:box-shadow .15s,border-color .15s;}"
                . ".fm-card:hover{border-color:#4a90d9;box-shadow:0 2px 8px rgba(74,144,217,.25);}"
                . ".fm-card b{font-size:14px;}"
                . ".fm-card span{font-size:11px;color:#777;}"
                . ".fm-card .fm-ico{font-size:20px;}"
                . ".fm-sep{display:flex;align-items:center;gap:10px;margin:14px 0;color:#999;font-size:12px;}"
                . ".fm-sep:before,.fm-sep:after{content:'';flex:1;border-top:1px solid #ddd;}"
                . "</style>";
            $html .= "<div class='fm-cards'>";
            foreach ($this->fmMeta as $key => $m) {
                $html .= "<button type='submit' name='fm_server' value='" . Adminer\h($key) . "' class='fm-card'>"
                    . "<span class='fm-ico'>" . $m['icon'] . "</span>"
                    . "<b>" . Adminer\h($m['title']) . "</b>"
                    . "<span>" . Adminer\h($m['sub']) . "</span>"
                    . "</button>";
            }
            $html .= "</div>";
            $html .= "<div class='fm-sep'>or login manually</div>";
            return $html . $heading . $value; // separator, then stock driver row
        }
        return null; // server/username/password/db: stock Adminer fields
    }
}

return new FMLoginServers();
