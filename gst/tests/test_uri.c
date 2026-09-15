// SPDX-License-Identifier: Apache-2.0
// The vgacapbin URI grammar: what each scheme becomes, and what is refused.
#include "gstvgacapuri.h"
#include "harness.h"

#define ASSERT_STR(got, want)                                                             \
    do {                                                                                  \
        const char *_g = (got), *_w = (want);                                             \
        if (g_strcmp0(_g, _w) != 0) {                                                     \
            harness_failures++;                                                           \
            fprintf(stderr, "  FAIL %s:%d: %s is \"%s\", expected \"%s\"\n", __FILE__,    \
                    __LINE__, #got, _g ? _g : "(null)", _w);                              \
            return;                                                                       \
        }                                                                                 \
    } while (0)

/* Parse @uri, expecting success; the caller clears @out. */
static gboolean parse_ok(const char *uri, VgaCapUri *out)
{
    GError *error = NULL;
    gboolean ok = vgacap_uri_parse(uri, out, &error);
    if (!ok) {
        fprintf(stderr, "  (uri '%s' was refused: %s)\n", uri, error->message);
        g_clear_error(&error);
    }
    return ok;
}

/* Parse @uri, expecting failure with @code; prints what came back instead. */
static gboolean refused(const char *uri, VgaCapUriError code)
{
    VgaCapUri parsed;
    GError *error = NULL;

    if (vgacap_uri_parse(uri, &parsed, &error)) {
        fprintf(stderr, "  (uri '%s' was accepted as %s %s=%s)\n", uri, parsed.element,
                parsed.property, parsed.value);
        vgacap_uri_clear(&parsed);
        return FALSE;
    }
    if (error == NULL || error->domain != VGACAP_URI_ERROR || error->code != (gint)code) {
        fprintf(stderr, "  (uri '%s' failed with the wrong error: %s)\n", uri,
                error ? error->message : "(none)");
        g_clear_error(&error);
        return FALSE;
    }
    /* Every refusal says which URI it is about, so the bus message is usable.
     * The two that have no URI to quote say so instead. */
    if (uri != NULL && *uri != '\0' && strstr(error->message, uri) == NULL) {
        fprintf(stderr, "  (error message does not quote the uri: %s)\n", error->message);
        g_clear_error(&error);
        return FALSE;
    }
    g_clear_error(&error);
    return TRUE;
}

static const char *param(const VgaCapUri *uri, const char *key)
{
    return g_hash_table_lookup(uri->params, key);
}

TEST(serial_becomes_a_link)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("tt-serial:///dev/ttyACM0", &uri));
    ASSERT_STR(uri.element, "vgacapttsrc");
    ASSERT_STR(uri.property, "link");
    ASSERT_STR(uri.value, "serial:/dev/ttyACM0");
    ASSERT_EQ_U(g_hash_table_size(uri.params), 0);
    vgacap_uri_clear(&uri);
}

TEST(serial_query_becomes_parameters)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("tt-serial:///dev/ttboard?project=tt_um_x&clock-hz=60000&seconds=2.5",
                         &uri));
    ASSERT_STR(uri.value, "serial:/dev/ttboard");
    ASSERT_EQ_U(g_hash_table_size(uri.params), 3);
    ASSERT_STR(param(&uri, "project"), "tt_um_x");
    ASSERT_STR(param(&uri, "clock-hz"), "60000");
    ASSERT_STR(param(&uri, "seconds"), "2.5");
    vgacap_uri_clear(&uri);
}

TEST(ws_keeps_host_port_and_path)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("tt-ws://welland:8765/serial?clock-hz=60000", &uri));
    ASSERT_STR(uri.element, "vgacapttsrc");
    ASSERT_STR(uri.value, "ws://welland:8765/serial");
    ASSERT_STR(param(&uri, "clock-hz"), "60000");
    vgacap_uri_clear(&uri);
}

TEST(ws_without_a_path_gets_the_bridge_endpoint)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("tt-ws://welland:8765", &uri));
    ASSERT_STR(uri.value, "ws://welland:8765/serial");
    vgacap_uri_clear(&uri);
}

TEST(ws_without_a_port_is_left_to_the_client)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("tt-ws://welland/serial", &uri));
    ASSERT_STR(uri.value, "ws://welland/serial");
    vgacap_uri_clear(&uri);
}

TEST(wss_is_a_secure_bridge)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("tt-wss://welland:8765/serial", &uri));
    ASSERT_STR(uri.value, "wss://welland:8765/serial");
    vgacap_uri_clear(&uri);
}

TEST(ipv6_host_keeps_its_brackets)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("tt-ws://[fe80::1]:8765/serial", &uri));
    ASSERT_STR(uri.value, "ws://[fe80::1]:8765/serial");
    vgacap_uri_clear(&uri);
}

TEST(file_becomes_a_filesrc)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("file:///captures/tt08.vgacap", &uri));
    ASSERT_STR(uri.element, "filesrc");
    ASSERT_STR(uri.property, "location");
    ASSERT_STR(uri.value, "/captures/tt08.vgacap");
    vgacap_uri_clear(&uri);
}

TEST(percent_escapes_are_decoded_once)
{
    VgaCapUri uri;
    /* The path is decoded; so is the query, and only once - the %2526 in the
     * value has to survive as %26, not collapse into an ampersand that would
     * split the parameter in two. */
    ASSERT_TRUE(parse_ok("file:///cap%20tures/a.vgacap?ttcap-command=a%2526b", &uri));
    ASSERT_STR(uri.value, "/cap tures/a.vgacap");
    ASSERT_EQ_U(g_hash_table_size(uri.params), 1);
    ASSERT_STR(param(&uri, "ttcap-command"), "a%26b");
    vgacap_uri_clear(&uri);
}

TEST(the_scheme_is_case_insensitive)
{
    VgaCapUri uri;
    ASSERT_TRUE(parse_ok("TT-Serial:///dev/ttyACM0", &uri));
    ASSERT_STR(uri.value, "serial:/dev/ttyACM0");
    vgacap_uri_clear(&uri);
}

TEST(bad_uris_are_refused)
{
    ASSERT_TRUE(refused(NULL, VGACAP_URI_ERROR_SYNTAX));
    ASSERT_TRUE(refused("", VGACAP_URI_ERROR_SYNTAX));
    ASSERT_TRUE(refused("/dev/ttyACM0", VGACAP_URI_ERROR_SYNTAX));       /* no scheme */
    ASSERT_TRUE(refused("http://example/x", VGACAP_URI_ERROR_SCHEME));   /* not ours */
    ASSERT_TRUE(refused("serial:/dev/ttyACM0", VGACAP_URI_ERROR_SCHEME)); /* the link, not a uri */
    ASSERT_TRUE(refused("tt-serial://", VGACAP_URI_ERROR_VALUE));        /* no device */
    ASSERT_TRUE(refused("tt-serial://host/dev/tty", VGACAP_URI_ERROR_VALUE)); /* two slashes */
    ASSERT_TRUE(refused("tt-ws:///serial", VGACAP_URI_ERROR_VALUE));     /* no host */
    ASSERT_TRUE(refused("file://", VGACAP_URI_ERROR_VALUE));             /* no path */
    ASSERT_TRUE(refused("file://elsewhere/x.vgacap", VGACAP_URI_ERROR_VALUE));
    ASSERT_TRUE(refused("tt-ws://welland:8765/serial#frames", VGACAP_URI_ERROR_VALUE));
    ASSERT_TRUE(refused("tt-ws://user@welland:8765/serial", VGACAP_URI_ERROR_VALUE));
    ASSERT_TRUE(refused("tt-ws://welland:8765/serial?clock-hz=%zz", VGACAP_URI_ERROR_SYNTAX));
}

int main(void)
{
    RUN(serial_becomes_a_link);
    RUN(serial_query_becomes_parameters);
    RUN(ws_keeps_host_port_and_path);
    RUN(ws_without_a_path_gets_the_bridge_endpoint);
    RUN(ws_without_a_port_is_left_to_the_client);
    RUN(wss_is_a_secure_bridge);
    RUN(ipv6_host_keeps_its_brackets);
    RUN(file_becomes_a_filesrc);
    RUN(percent_escapes_are_decoded_once);
    RUN(the_scheme_is_case_insensitive);
    RUN(bad_uris_are_refused);
    RUN_TESTS_END();
}
