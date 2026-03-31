// shm_redirect.c -- LD_PRELOAD shim for POSIX semaphores on AWS Lambda
//
// AWS Lambda has no /dev/shm. Python's multiprocessing module calls sem_open(),
// which on glibc requires /dev/shm to be a tmpfs mount. When it isn't, glibc
// returns ENOSYS without ever making a syscall, and multiprocessing fails.
//
// This shim intercepts sem_open, sem_close, and sem_unlink and reimplements
// them using file-backed mmap under /tmp/shm/ (which Lambda does provide).
// The semaphore itself uses sem_init with pshared=1 (futex-based), so no
// shared memory filesystem is needed after the backing file is created.
//
// Compile: gcc -shared -fPIC -O2 -o shm_redirect.so shm_redirect.c -ldl -lpthread
// Use:     LD_PRELOAD=./shm_redirect.so python3 your_script.py

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <semaphore.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *SHM_DIR = "/tmp/shm";

// Create /tmp/shm on first use. Lambda starts with an empty /tmp.
static void ensure_dir(void) {
    static int done = 0;
    if (!done) { mkdir(SHM_DIR, 0777); done = 1; }
}

// Intercept sem_open: create a file-backed semaphore in /tmp/shm.
//
// CPython's _multiprocessing.SemLock calls sem_open(name, O_CREAT|O_EXCL, 0600, value).
// We open a regular file, size it to hold a sem_t, mmap it MAP_SHARED, and call
// sem_init(pshared=1) which uses futex -- no /dev/shm required.
sem_t *sem_open(const char *name, int oflag, ...) {
    ensure_dir();

    // Build path: /tmp/shm/sem.<name>
    char path[512];
    const char *n = (name[0] == '/') ? name + 1 : name;
    snprintf(path, sizeof(path), "%s/sem.%s", SHM_DIR, n);

    mode_t mode = 0600;
    unsigned int value = 0;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, unsigned int);
        value = va_arg(ap, unsigned int);
        va_end(ap);
    }

    // O_RDWR is required for ftruncate + mmap(PROT_WRITE).
    // CPython passes O_CREAT|O_EXCL without O_RDWR, so we add it.
    int fd = open(path, oflag | O_RDWR, mode);
    if (fd < 0)
        return SEM_FAILED;

    // If the file is new (empty), expand it to hold a sem_t.
    struct stat st;
    fstat(fd, &st);
    int need_init = (st.st_size < (off_t)sizeof(sem_t));
    if (need_init && ftruncate(fd, sizeof(sem_t)) < 0) {
        close(fd);
        return SEM_FAILED;
    }

    sem_t *sem = (sem_t *)mmap(NULL, sizeof(sem_t),
                                PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (sem == MAP_FAILED)
        return SEM_FAILED;

    // Initialize as a process-shared semaphore (futex-based, no shm needed).
    if (need_init || (oflag & O_EXCL))
        sem_init(sem, 1, value);

    return sem;
}

int sem_close(sem_t *sem) {
    return munmap(sem, sizeof(sem_t));
}

int sem_unlink(const char *name) {
    char path[512];
    const char *n = (name[0] == '/') ? name + 1 : name;
    snprintf(path, sizeof(path), "%s/sem.%s", SHM_DIR, n);
    return unlink(path);
}
