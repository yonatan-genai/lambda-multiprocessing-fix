.PHONY: build test clean

build:
	gcc -shared -fPIC -O2 -o shm_redirect.so shm_redirect.c -ldl -lpthread

test: build
	@echo "Running tests..."
	python3 test_shm.py

clean:
	rm -f shm_redirect.so
