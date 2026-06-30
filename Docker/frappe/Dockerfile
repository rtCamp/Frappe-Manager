FROM ubuntu:22.04 AS base

LABEL author=rtCamp
LABEL org.opencontainers.image.source=https://github.com/rtcamp/Frappe-Manager

COPY find-best-mirror.sh /tmp/find-best-mirror.sh

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y curl ca-certificates \
    && chmod +x /tmp/find-best-mirror.sh \
    && /tmp/find-best-mirror.sh \
    && rm /tmp/find-best-mirror.sh \
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
    git \
    mariadb-client \
    postgresql-client \
    gettext-base \
    wget \
    xfonts-75dpi \
    xfonts-base \
    libssl-dev \
    fonts-cantarell \
    libpangocairo-1.0-0 \
    locales \
    build-essential \
    cron \
    vim \
    sudo \
    iputils-ping \
    watch \
    tree \
    nano \
    less \
    software-properties-common \
    bash-completion \
    libpq-dev \
    libffi-dev \
    liblcms2-dev \
    libldap2-dev \
    libmariadb-dev \
    libsasl2-dev \
    libtiff5-dev \
    libwebp-dev \
    redis-tools \
    rlwrap \
    tk8.6-dev \
    ssh-client \
    net-tools \
    make \
    libbz2-dev \
    libsqlite3-dev \
    zlib1g-dev \
    libreadline-dev \
    llvm \
    libncurses5-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    liblzma-dev \
    wait-for-it \
    supervisor \
    psmisc \
    jq \
    gosu \
    fonts-powerline \
    file \
    unzip \
    && rm -rf /var/lib/apt/lists/*

RUN sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && dpkg-reconfigure --frontend=noninteractive locales

ENV WKHTMLTOPDF_VERSION=0.12.6.1-3
RUN ARCH="$(uname -m)" && \
    if [ "$ARCH" = "aarch64" ]; then ARCH=arm64; fi && \
    if [ "$ARCH" = "x86_64" ]; then ARCH=amd64; fi && \
    downloaded_file=wkhtmltox_$WKHTMLTOPDF_VERSION.jammy_${ARCH}.deb && \
    wget -q https://github.com/wkhtmltopdf/packaging/releases/download/$WKHTMLTOPDF_VERSION/$downloaded_file && \
    dpkg -i $downloaded_file && \
    rm $downloaded_file


FROM base AS tooling

ARG FNM_VERSION='1.38.1'
ARG DEFAULT_PYTHON_VERSION='3.14.2'
ARG DEFAULT_NODE_VERSION='24.11.0'
SHELL ["/bin/bash", "-c"]

ENV FNM_VERSION=${FNM_VERSION}
ENV DEFAULT_PYTHON_VERSION=${DEFAULT_PYTHON_VERSION}
ENV DEFAULT_NODE_VERSION=${DEFAULT_NODE_VERSION}
ENV BENCH_USE_UV=true

# setup user
RUN export NAME='frappe' && \
    groupadd -g 1000 $NAME && \
    useradd --no-log-init -r -m -u 1000 -g 1000 -G sudo -s /bin/bash -d /workspace "$NAME" && \
    usermod -a -G tty "$NAME" && \
    echo "$NAME ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

# for bench wrapper
ENV PATH=/opt/user/.bin:${PATH}

COPY --chmod=0755 ./helper-function.sh /scripts/

RUN mkdir -p /opt/user/conf.d && chown -R frappe:frappe /opt/user

USER frappe

WORKDIR /opt
COPY --chown=frappe:frappe ./fmx/pyproject.toml ./fmx/uv.lock ./fmx/fmx/ /opt/user/fmx/
RUN mkdir -p /workspace && touch /opt/user/.hushlogin

USER root
RUN mkdir -p /etc/bash.bashrc.d
COPY ./bashrc /etc/bash.bashrc.d/fm.bashrc
RUN echo '' >> /etc/bash.bashrc \
    && echo 'if [ -f /etc/bash.bashrc.d/fm.bashrc ]; then' >> /etc/bash.bashrc \
    && echo '    . /etc/bash.bashrc.d/fm.bashrc' >> /etc/bash.bashrc \
    && echo 'fi' >> /etc/bash.bashrc
# Install UV system-wide as root so it's available to all users
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

# Configure UV to install tools in a shared, non-persistent location
ENV UV_TOOL_DIR=/opt/uv-tools
ENV UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv tool install --python 3.10 frappe-bench \
    && uv tool install --python 3.10 --from /opt/user/fmx fmx \
    && rm -rf /opt/user/fmx \
    && chmod -R 755 /opt/uv-tools

# Install fnm (Fast Node Manager) - binary only, Node versions installed later
RUN echo "Installing fnm v${FNM_VERSION}..." && \
    curl -fsSL https://fnm.vercel.app/install | bash -s -- --install-dir /tmp/fnm-install --skip-shell && \
    mv /tmp/fnm-install/fnm /usr/local/bin/fnm && \
    chmod +x /usr/local/bin/fnm && \
    rm -rf /tmp/fnm-install

RUN mkdir -p /opt/user/.bin

COPY --chown=frappe:frappe ./supervisord.conf /opt/user/
COPY --chown=frappe:frappe ./frappe-dev.conf /opt/user/
COPY --chown=frappe:frappe --chmod=0755 ./bench-dev-watch.sh /opt/user/
COPY --chown=frappe:frappe --chmod=0755 ./bench-dev-server /opt/user/
COPY --chown=frappe:frappe --chmod=0755 ./bench-wrapper.sh /opt/user/.bin/bench

FROM tooling AS frappe

ENV UV_PYTHON_INSTALL_DIR=/workspace/frappe-bench/.uv/python
ENV UV_CACHE_DIR=/workspace/frappe-bench/.uv/cache

# fnm configuration - CRITICAL: FNM_COREPACK_ENABLED ensures corepack is enabled for ALL Node versions
ENV FNM_DIR=/workspace/frappe-bench/.fnm
ENV FNM_NODE_DIST_MIRROR=https://nodejs.org/dist
ENV FNM_MULTISHELL_PATH=/workspace/frappe-bench/.fnm
ENV FNM_COREPACK_ENABLED=true
ENV PATH="/workspace/frappe-bench/.fnm/aliases/default/bin:/usr/local/bin:/opt/user/.bin:${PATH}"

ENV BASH_ENV=/etc/profile.d/fnm.sh
ENV ENV=/etc/profile.d/fnm.sh

# Prebake Python 3.12 and Node 22 into /workspace for faster first site creation
USER frappe

RUN mkdir -p /workspace/frappe-bench/.uv/python /workspace/frappe-bench/.fnm /workspace/frappe-bench/.uv/cache && \
    echo "Prebaking Python ${DEFAULT_PYTHON_VERSION}..." && \
    uv python install ${DEFAULT_PYTHON_VERSION} && \
    PYTHON_PATH=$(uv python find ${DEFAULT_PYTHON_VERSION}) && \
    PYTHON_DIR=$(dirname $(dirname $PYTHON_PATH)) && \
    ln -sf python/$(basename $PYTHON_DIR) /workspace/frappe-bench/.uv/python-default && \
    echo "Prebaking Node ${DEFAULT_NODE_VERSION} (corepack auto-enabled via FNM_COREPACK_ENABLED)..." && \
    fnm install ${DEFAULT_NODE_VERSION} && \
    fnm default ${DEFAULT_NODE_VERSION} && \
    echo "Making cache directories writable for any runtime UID..." && \
    chmod -R 777 /workspace/frappe-bench/.uv/cache

ENV COREPACK_HOME=/workspace/frappe-bench/.fnm/corepack

USER root

COPY --chmod=0755 ./entrypoint.sh /
COPY --chmod=0755 ./exec-entrypoint.sh /
COPY --chmod=0755 ./user-script.sh /scripts/
COPY --chmod=0755 ./launch_supervisor_service.sh /scripts/

WORKDIR /workspace

COPY --from=tooling --chown=frappe:frappe /opt/user /opt/user
COPY --from=tooling /opt/uv-tools /opt/uv-tools
COPY --from=tooling /etc/bash.bashrc /etc/bash.bashrc

# Setup fnm and python for ALL shell types (login, non-login, interactive, non-interactive)
# Respect existing env vars — only use defaults if not already set
COPY --chmod=755 ./fnm.sh /etc/profile.d/fnm.sh
RUN echo '' >> /etc/bash.bashrc && \
    echo '. /etc/profile.d/fnm.sh' >> /etc/bash.bashrc

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]
