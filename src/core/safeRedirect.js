// safeRedirect.js — where the app may send the browser after login (?redirect=...).
//
// The login shell honours /?redirect=<path> so a protected page or upload that bounced a
// browser with a stale session can send it straight back. The target must stay on THIS
// origin. A plain startsWith('/') test is not enough: '//other.host' and '/\other.host'
// (browsers read '\' as '/') are protocol-relative, and the URL parser deletes tab and
// newline characters, so '/<TAB>/other.host' also becomes '//other.host'. Resolve the
// target with the same parser the navigation will use, then keep it only if the origin
// is unchanged.

export function safeRedirectTarget(raw, origin) {
    if (typeof raw !== 'string' || !raw.startsWith('/')) return null;
    let base;
    let target;
    try {
        base = new URL(origin);
        target = new URL(raw, base);
    } catch (e) {
        return null;
    }
    if (target.origin !== base.origin) return null;
    return target.pathname + target.search + target.hash;
}
