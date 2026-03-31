# lambda-multiprocessing-fix

Fix Python `multiprocessing` on AWS Lambda (AL2023). Supports Lock, Queue, Pool, Semaphore, and shared_memory without changing your code.

## The problem

AWS Lambda doesn't mount `/dev/shm`. Python's `multiprocessing` module relies on it for POSIX semaphores, which means `Lock`, `Queue`, `Pool`, and `Semaphore` all fail with:

```
OSError: [Errno 38] Function not implemented
```

Under the hood, glibc's `sem_open()` checks that `/dev/shm` exists as a tmpfs mount. When it doesn't find one, it gives up and returns `ENOSYS` before even attempting a kernel call. Your code is fine, the OS just won't cooperate.

This is especially painful when you're using pre-compiled packages or third-party libraries that use multiprocessing internally (PyTorch DataLoader, pandas parallel ops, scientific computing libraries, static analysis tools like CodeQL). You can't edit their code to work around the limitation, so you need a fix at the OS level.

## How it works

`shm_redirect.so` is a small C library (~80 lines) that loads before Python starts. It replaces three functions (`sem_open`, `sem_close`, `sem_unlink`) with versions that store semaphore data in `/tmp/shm/` instead of `/dev/shm/`.

It works by creating a regular file, sizing it to hold a semaphore struct, memory-mapping it with `MAP_SHARED` so multiple processes can access it, and initializing a kernel futex. The behavior is identical to a normal `sem_open`, just backed by `/tmp` which Lambda actually allows you to write to.

You load it by setting the `LD_PRELOAD` environment variable. This tells Linux's dynamic linker to load our library before everything else. When Python (or any program) calls `sem_open`, it hits our version instead of glibc's. The calling code has no idea anything changed.

## Quick start

**1. Compile the shared library**

```bash
gcc -shared -fPIC -O2 -o shm_redirect.so shm_redirect.c -ldl -lpthread
```

Or just run `make build`.

**2. Set the environment variable**

```bash
export LD_PRELOAD=/path/to/shm_redirect.so
```

**3. Use multiprocessing normally**

```python
from multiprocessing import Pool

with Pool(4) as p:
    results = p.map(str.upper, ["hello", "from", "lambda"])
```

That's it. No code changes, no special imports, no monkey-patching.

## Docker (Lambda container images)

Add this to your Dockerfile:

```dockerfile
FROM public.ecr.aws/lambda/python:3.12

# Install gcc, build the fix, clean up
RUN dnf install -y gcc && dnf clean all
COPY shm_redirect.c /opt/lib/
RUN gcc -shared -fPIC -O2 -o /opt/lib/shm_redirect.so /opt/lib/shm_redirect.c -ldl -lpthread

ENV LD_PRELOAD=/opt/lib/shm_redirect.so

COPY app.py .
CMD ["app.handler"]
```

See `Dockerfile.example` for a complete working example.

If you don't want gcc in your final image, use a multi-stage build:

```dockerfile
FROM public.ecr.aws/lambda/python:3.12 AS builder
RUN dnf install -y gcc && dnf clean all
COPY shm_redirect.c /tmp/
RUN gcc -shared -fPIC -O2 -o /tmp/shm_redirect.so /tmp/shm_redirect.c -ldl -lpthread

FROM public.ecr.aws/lambda/python:3.12
COPY --from=builder /tmp/shm_redirect.so /opt/lib/shm_redirect.so
ENV LD_PRELOAD=/opt/lib/shm_redirect.so
```

## Lambda Layer (zip-based deployments)

If you're deploying Lambda as a zip file instead of a container, package the `.so` as a Lambda Layer.

**Build the layer (must compile on Amazon Linux 2023 or equivalent):**

```bash
# In a Docker container matching Lambda's runtime:
docker run --rm -v $(pwd):/build public.ecr.aws/lambda/python:3.12 \
    bash -c "dnf install -y gcc && gcc -shared -fPIC -O2 -o /build/shm_redirect.so /build/shm_redirect.c -ldl -lpthread"

# Package for Lambda Layer
mkdir -p layer/lib
cp shm_redirect.so layer/lib/
cd layer && zip -r ../shm-layer.zip .
```

**Deploy the layer:**

```bash
aws lambda publish-layer-version \
    --layer-name shm-redirect \
    --zip-file fileb://shm-layer.zip \
    --compatible-runtimes python3.10 python3.11 python3.12 python3.13

aws lambda update-function-configuration \
    --function-name your-function \
    --layers arn:aws:lambda:REGION:ACCOUNT:layer:shm-redirect:1 \
    --environment "Variables={LD_PRELOAD=/opt/lib/shm_redirect.so}"
```

## Verify it works

Run the included test suite:

```bash
make test
```

This tests `Lock`, `Semaphore`, `Queue`, `Pool`, `Value`/`Array`, and `shared_memory`. On Linux, the test script automatically sets `LD_PRELOAD` if it's not already set.

## When you'd want this

The main use case is running pre-compiled packages that use multiprocessing internally. If you control all the code, you can rewrite things to use `multiprocessing.Pipe` or threads instead. But if you're running something like PyTorch DataLoader, a scientific computing library, or a static analysis tool that spawns worker processes, you can't change how they synchronize. This fix works at the OS level, below Python, so it handles everything transparently.

## Limitations

- **AWS Lambda on Amazon Linux 2023.** Built and tested for Lambda's execution environment (glibc 2.34, x86_64 and arm64).
- **Tested on glibc 2.34+ (AL2023).** It should work on older glibc versions but hasn't been tested. If you're on AL2 (glibc 2.26), try it and open an issue if something breaks.
- **Backed by `/tmp`, not tmpfs.** Lambda's `/tmp` is a real ext4 filesystem (up to 10 GB with ephemeral storage). For typical semaphore usage this doesn't matter since semaphore files are tiny and stay in the page cache. You won't notice a performance difference.
- **`/tmp` is per-execution-environment.** Each Lambda execution environment gets its own `/tmp`, so semaphore names are already isolated between concurrent invocations.

## How is this different from other workarounds?

| Approach | What it fixes | Code changes needed | Works with third-party packages |
|----------|--------------|--------------------|-----------------------------|
| **This library** | Lock, Queue, Pool, Semaphore, shared_memory | None. Set `LD_PRELOAD` and go. | Yes. Any library, any framework. |
| Rewrite your code to use Pipe/threads | Everything (in theory) | Rewrite all multiprocessing usage | No. You'd need to fork and patch every dependency that uses multiprocessing internally. |
| Pipe-based Pool replacements | Pool only | Change imports | No. Only covers Pool, not Lock/Queue/Semaphore. |
| Python monkey-patching | Lock (maybe) | Inject code via sitecustomize.py | Fragile. Breaks across CPython versions. |

If you control all the code, you can rewrite things to avoid `multiprocessing.Lock`/`Queue`/`Pool` and use pipes or threads instead. That's a legitimate approach for simple cases. But when you're running pre-compiled packages from PyPI, ML frameworks, or tools like CodeQL that use multiprocessing internally, you can't change how they synchronize. This library fixes the underlying OS primitive, so the entire `multiprocessing` module works unchanged, including inside dependencies you didn't write.

## License

MIT. See [LICENSE](LICENSE).
