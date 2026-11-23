#!/usr/bin/env bash
# Clone the pinned CTFd release into ./vendor/CTFd (the docker build context).
set -euo pipefail
CTFD_VERSION="${CTFD_VERSION:-3.8.9}"
cd "$(dirname "$0")/.."
if [ -d vendor/CTFd/.git ]; then
  echo "vendor/CTFd already present"
  exit 0
fi
mkdir -p vendor
git clone --depth 1 --branch "$CTFD_VERSION" https://github.com/CTFd/CTFd.git vendor/CTFd
