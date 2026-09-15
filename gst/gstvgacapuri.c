/* SPDX-License-Identifier: Apache-2.0 */
#include "gstvgacapuri.h"

#include <string.h>

/* The path a tt-ws URI gets when it names only a host: the bridge's serial
 * endpoint, which is the only one a capture ever talks to. */
#define WS_DEFAULT_PATH "/serial"

GQuark vgacap_uri_error_quark(void)
{
    return g_quark_from_static_string("vgacap-uri-error");
}

void vgacap_uri_clear(VgaCapUri *uri)
{
    if (!uri)
        return;
    g_clear_pointer(&uri->element, g_free);
    g_clear_pointer(&uri->property, g_free);
    g_clear_pointer(&uri->value, g_free);
    g_clear_pointer(&uri->params, g_hash_table_unref);
}

/* `host`, bracketed again if it is a bare IPv6 address: g_uri_split strips
 * the brackets, and ws://::1:8765/ would be unreadable to anything. */
static gchar *authority_text(const gchar *host, gint port)
{
    gboolean v6 = host && strchr(host, ':') != NULL;
    if (port > 0)
        return g_strdup_printf(v6 ? "[%s]:%d" : "%s:%d", host, port);
    return g_strdup_printf(v6 ? "[%s]" : "%s", host);
}

gboolean vgacap_uri_parse(const gchar *uri, VgaCapUri *out, GError **error)
{
    gchar *scheme = NULL, *userinfo = NULL, *host = NULL, *path = NULL;
    gchar *query = NULL, *fragment = NULL, *lower = NULL;
    GHashTable *params = NULL;
    GError *inner = NULL;
    VgaCapUri built;
    gint port = -1;
    gboolean ok = FALSE;

    g_return_val_if_fail(out != NULL, FALSE);
    memset(&built, 0, sizeof built);

    if (!uri || !*uri) {
        g_set_error_literal(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_SYNTAX,
                            "the uri property is not set");
        return FALSE;
    }
    /* ENCODED_QUERY, not NONE: g_uri_parse_params does its own %-decoding, so
     * handing it an already-decoded query would decode twice and split a
     * value containing a literal %26 into two parameters. */
    if (!g_uri_split(uri, G_URI_FLAGS_ENCODED_QUERY, &scheme, &userinfo, &host, &port,
                     &path, &query, &fragment, &inner)) {
        g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_SYNTAX,
                    "cannot parse '%s' as a URI: %s", uri, inner->message);
        g_clear_error(&inner);
        return FALSE;
    }
    if (!scheme) {
        g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_SYNTAX,
                    "'%s' has no scheme; expected tt-serial:, tt-ws: or file:", uri);
        goto out;
    }
    if (userinfo) {
        g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_VALUE,
                    "'%s' carries user information, which no vgacap link uses", uri);
        goto out;
    }
    if (fragment) {
        /* Refusing rather than ignoring: a fragment here is always someone
         * expecting it to mean something, and silently dropping it would
         * make a capture start with the wrong settings. */
        g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_VALUE,
                    "'%s' has a fragment, which means nothing to a capture", uri);
        goto out;
    }

    params = g_uri_parse_params(query ? query : "", -1, "&", G_URI_PARAMS_NONE, &inner);
    if (!params) {
        g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_SYNTAX,
                    "cannot parse the query of '%s': %s", uri, inner->message);
        g_clear_error(&inner);
        goto out;
    }

    lower = g_ascii_strdown(scheme, -1);

    if (g_strcmp0(lower, "file") == 0) {
        if (host && *host && g_ascii_strcasecmp(host, "localhost") != 0) {
            g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_VALUE,
                        "'%s' is a file on another host ('%s'), which cannot be opened",
                        uri, host);
            goto out;
        }
        if (!path || !*path) {
            g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_VALUE,
                        "'%s' names no file", uri);
            goto out;
        }
        built.element = g_strdup("filesrc");
        built.property = g_strdup("location");
        built.value = g_strdup(path);
    } else if (g_strcmp0(lower, "tt-serial") == 0) {
        if (host && *host) {
            g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_VALUE,
                        "'%s' names the host '%s': tt-serial takes a device path, so "
                        "write tt-serial:///dev/ttyACM0 with three slashes", uri, host);
            goto out;
        }
        if (!path || !*path) {
            g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_VALUE,
                        "'%s' names no serial device", uri);
            goto out;
        }
        built.element = g_strdup("vgacapttsrc");
        built.property = g_strdup("link");
        built.value = g_strdup_printf("serial:%s", path);
    } else if (g_strcmp0(lower, "tt-ws") == 0 || g_strcmp0(lower, "tt-wss") == 0) {
        gchar *authority;
        if (!host || !*host) {
            g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_VALUE,
                        "'%s' names no bridge host", uri);
            goto out;
        }
        authority = authority_text(host, port);
        built.element = g_strdup("vgacapttsrc");
        built.property = g_strdup("link");
        built.value = g_strdup_printf("%s://%s%s",
                                      g_strcmp0(lower, "tt-wss") == 0 ? "wss" : "ws",
                                      authority,
                                      (path && *path) ? path : WS_DEFAULT_PATH);
        g_free(authority);
    } else {
        g_set_error(error, VGACAP_URI_ERROR, VGACAP_URI_ERROR_SCHEME,
                    "'%s' has the unknown scheme '%s'; expected tt-serial, tt-ws, "
                    "tt-wss or file", uri, lower);
        goto out;
    }

    built.params = g_steal_pointer(&params);
    *out = built;
    memset(&built, 0, sizeof built);
    ok = TRUE;

out:
    vgacap_uri_clear(&built);
    g_clear_pointer(&params, g_hash_table_unref);
    g_free(lower);
    g_free(scheme);
    g_free(userinfo);
    g_free(host);
    g_free(path);
    g_free(query);
    g_free(fragment);
    return ok;
}
