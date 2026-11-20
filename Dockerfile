FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS engine
RUN rm /etc/apt/sources.list.d/debian.sources && \
    echo 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261101T000000Z bookworm main' > /etc/apt/sources.list && \
    echo 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20261101T000000Z bookworm-security main' >> /etc/apt/sources.list && \
    echo 'Acquire::Retries "4";' > /etc/apt/apt.conf.d/80retries && \
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        texlive-luatex texlive-lang-korean texlive-latex-recommended fonts-nanum && \
    rm -rf /var/lib/apt/lists/*

FROM engine AS backend
WORKDIR /opt/jjokgyeol
COPY requirements.lock ./
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock && pip check && \
    useradd --uid 10001 --create-home editor
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/jjokgyeol

FROM backend AS hwp-deps
COPY packaging/hwp/ /tmp/hwp/
RUN --network=none pip install --no-index --find-links=/tmp/hwp --require-hashes --no-compile \
    --target=/opt/hwp -r /tmp/hwp/requirements.lock

FROM backend AS test-tools
COPY packaging/requirements-test.lock packaging/
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r packaging/requirements-test.lock && pip check
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libatomic1 poppler-utils && \
    playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

FROM backend AS service
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends poppler-utils && \
    rm -rf /var/lib/apt/lists/*
COPY --from=hwp-deps /opt/hwp/ /usr/local/lib/python3.12/site-packages/
COPY runtime.lock ./
COPY packaging/check_runtime.py packaging/
RUN python packaging/check_runtime.py runtime.lock
COPY proofs/engine-comparison/manuscript.md proofs/engine-comparison/
COPY app/ app/
USER editor
EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]

FROM test-tools AS test
COPY packaging/decision_bundle.py packaging/
COPY --from=service /opt/jjokgyeol/ ./
COPY --from=hwp-deps /opt/hwp/ /usr/local/lib/python3.12/site-packages/
COPY tests/ tests/
COPY proofs/duplex-running-heads/*.docx proofs/duplex-running-heads/*.hwpx proofs/duplex-running-heads/
COPY proofs/reference-style/*.docx proofs/reference-style/*.hwpx proofs/reference-style/
COPY proofs/running-heads/*.docx proofs/running-heads/*.hwpx proofs/running-heads/
COPY evaluation/ evaluation/
COPY proofs/engine-comparison/LuaLaTeX.pdf proofs/engine-comparison/
COPY proofs/running-heads/style.json proofs/running-heads/
ENV PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache"
USER editor
RUN --network=none pytest -q tests/test_runtime.py --basetemp=/tmp/t2-calibration && \
    cp /tmp/t2-calibration/ruler0/ruler.pdf /home/editor/ruler.pdf && rm -rf /tmp/t2-calibration
CMD ["pytest", "-q"]

FROM service AS local
