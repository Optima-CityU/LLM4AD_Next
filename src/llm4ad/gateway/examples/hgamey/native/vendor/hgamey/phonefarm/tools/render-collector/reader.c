/* Fixed receiver-owned witness byte reader. Read-only files, bounded snapshots.
 * NUL triples: operation, record name, payload/path; final triple end,"","".
 * The fixed shell creates requests; this tool never interprets a shell command.
 */
#define _GNU_SOURCE
#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define RECORD_CAP 65536u
#define STREAM_CAP (2u * 1024u * 1024u)
#define REQUEST_CAP 2048u
static size_t emitted;
static int failed;
static int fail(const char *reason) {
    fprintf(stderr, "render_reader: %s\n", reason);
    failed = 1;
    return 81;
}
static int emit(const void *bytes, size_t n) {
    if (failed || n > STREAM_CAP - emitted) return fail("output exceeds 2MiB");
    if (n && fwrite(bytes, 1, n, stdout) != n) return fail("output write failed");
    emitted += n;
    return 0;
}
static int literal(const char *s) { return emit(s, strlen(s)); }
static int name_ok(const char *s) {
    size_t n = strlen(s);
    if (!n || n > 128) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '/' || c == '.')) return 0;
    }
    return 1;
}
static int snapshot(const char *path, unsigned char **out, size_t *len) {
    if (path[0] != '/' || strlen(path) > 1024 || strchr(path, '\n')) return fail("invalid fixed file path");
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return fail("record open failed");
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 0 || st.st_size > RECORD_CAP) {
        close(fd); return fail("record metadata invalid/oversize");
    }
    unsigned char *b = malloc(RECORD_CAP + 1u);
    if (!b) { close(fd); return fail("allocation failed"); }
    size_t n = 0;
    while (n <= RECORD_CAP) {
        ssize_t r = read(fd, b + n, RECORD_CAP + 1u - n);
        if (r > 0) { n += (size_t)r; continue; }
        if (r == 0) break;
        if (errno == EINTR) continue;
        close(fd); free(b); return fail("record read failed");
    }
    if (close(fd)) { free(b); return fail("record close failed"); }
    if (n > RECORD_CAP) { free(b); return fail("record exceeds 64KiB"); }
    *out = b; *len = n; return 0;
}
static int record(const char *op, const char *name, const char *payload) {
    if (!name_ok(name)) return fail("invalid record name");
    unsigned char *owned = NULL;
    const unsigned char *data = (const unsigned char *)payload;
    size_t len = strlen(payload);
    if (strcmp(op, "value")) {
        if (snapshot(payload, &owned, &len)) return 81;
        data = owned;
    }
    if (!strcmp(op, "block")) {
        if (!len || data[len-1] != '\n' || memchr(data, 0, len)) {
            free(owned); return fail("mandatory text empty/unterminated/NUL");
        }
    } else if (!strcmp(op, "filter")) {
        if ((len && data[len-1] != '\n') || memchr(data, 0, len)) {
            free(owned); return fail("filter text unterminated/NUL");
        }
        while (len && data[len-1] == '\n') len--;
    } else if (!strcmp(op, "cmdline")) {
        if (!len) { free(owned); return fail("cmdline empty"); }
        for (size_t i = 0; i < len; i++) if (!owned[i]) owned[i] = '\n';
        while (len && data[len-1] == '\n') len--;
    } else if (strcmp(op, "value")) {
        free(owned); return fail("unknown request operation");
    }
    if (len > RECORD_CAP) { free(owned); return fail("value exceeds 64KiB"); }
    int rc = literal("BEGIN\t") || literal(name) || literal("\n");
    if (!rc && !strcmp(op, "filter")) rc = literal("EMPTY");
    if (!rc) rc = emit(data, len);
    if (!rc) rc = literal("\nEND\t") || literal(name) || literal("\n");
    free(owned);
    return rc ? 81 : 0;
}
static char *field(unsigned char *b, size_t n, size_t *at) {
    if (*at >= n) return NULL;
    unsigned char *p = memchr(b + *at, 0, n - *at);
    if (!p) return NULL;
    char *s = (char *)(b + *at);
    *at = (size_t)(p - b) + 1u;
    return s;
}
int main(void) {
    static char buffer[65536];
    if (setvbuf(stdout, buffer, _IOFBF, sizeof(buffer))) return fail("output buffer init failed");
    unsigned char *requests = malloc(STREAM_CAP + 1u);
    if (!requests) return fail("allocation failed");
    size_t n = 0;
    while (n <= STREAM_CAP) {
        size_t r = fread(requests+n, 1, STREAM_CAP+1u-n, stdin);
        n += r;
        if (!r) break;
    }
    if (ferror(stdin) || n > STREAM_CAP) { free(requests); return fail("request stream failed/oversize"); }
    size_t at = 0, count = 0;
    int ended = 0;
    /* Validate framing before reading any records or emitting success bytes. */
    while (at < n) {
        char *op=field(requests,n,&at), *name=field(requests,n,&at), *arg=field(requests,n,&at);
        if (!op || !name || !arg || ++count > REQUEST_CAP) { free(requests); return fail("request framing incomplete/oversize"); }
        if (!strcmp(op,"end")) {
            if (*name || *arg || at != n) { free(requests); return fail("terminal request invalid"); }
            ended=1; break;
        }
        if ((!strcmp(op,"value") || !strcmp(op,"block") || !strcmp(op,"filter") || !strcmp(op,"cmdline")) && name_ok(name) && strlen(arg)<=RECORD_CAP) continue;
        free(requests); return fail("request type/name/value invalid");
    }
    if (!ended) { free(requests); return fail("missing terminal request"); }
    if (literal("#phonefarm_render_witness v2\n")) { free(requests); return 81; }
    at=0;
    while (at<n) {
        char *op=field(requests,n,&at), *name=field(requests,n,&at), *arg=field(requests,n,&at);
        if (!strcmp(op,"end")) break;
        if (record(op,name,arg)) { free(requests); return 81; }
    }
    free(requests);
    if (literal("#end\n") || fflush(stdout)) return fail("terminal output flush failed");
    return 0;
}
