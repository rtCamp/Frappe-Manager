FROM ubuntu:22.04 AS base

LABEL author=rtCamp
LABEL org.opencontainers.image.source=https://github.com/rtcamp/Frappe-Manager

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
    # For frappe framework
    git \
    mariadb-client \
    postgresql-client \
    gettext-base \
    wget \
    # for PDF
    xfonts-75dpi \
    xfonts-base \
    libssl-dev \
    fonts-cantarell \
    libpangocairo-1.0-0 \
    # to work inside the container
    locales \
    build-essential \
    cron \
    curl \
    vim \
    sudo \
    iputils-ping \
    watch \
    tree \
    nano \
    less \
    software-properties-common \
    bash-completion \
    # For psycopg2
    libpq-dev \
    # Other
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
    zsh \
    file \
    unzip \
    && rm -rf /var/lib/apt/lists/*

RUN sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && dpkg-reconfigure --frontend=noninteractive locales

FROM base AS tooling

ARG FNM_VERSION='1.38.1'
ARG DEFAULT_PYTHON_VERSION='3.12.0'
ARG DEFAULT_NODE_VERSION='22.18.0'
SHELL ["/bin/bash", "-c"]

ENV FNM_VERSION=${FNM_VERSION}
ENV DEFAULT_PYTHON_VERSION=${DEFAULT_PYTHON_VERSION}
ENV DEFAULT_NODE_VERSION=${DEFAULT_NODE_VERSION}
ENV BENCH_USE_UV=true

# setup user
RUN export NAME='frappe' && \
    groupadd -g 1000 $NAME && \
    useradd --no-log-init -r -m -u 1000 -g 1000 -G sudo -s /usr/bin/zsh -d /workspace "$NAME" && \
    usermod -a -G tty "$NAME" && \
    echo "$NAME ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

# for bench wrapper
ENV PATH=/opt/user/.bin:${PATH}

COPY --chmod=0755 ./helper-function.sh /scripts/

RUN mkdir -p /opt/user/conf.d && chown -R frappe:frappe /opt/user

USER frappe
WORKDIR /opt
RUN touch /opt/user/.profile
ENV ZSH=/workspace/.oh-my-zsh
ENV DISABLE_UPDATE_PROMPT=true
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
RUN unset ZSH
COPY --chown=frappe:frappe ./zshrc /opt/user/.zshrc
COPY --chown=frappe:frappe ./fm.zsh-theme /workspace/.oh-my-zsh/custom/themes/fm.zsh-theme
COPY --chown=frappe:frappe ./fmx /opt/user/fmx
RUN mkdir -p /workspace

USER root
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

ENV UV_PYTHON_INSTALL_DIR=/workspace/.uv/python
ENV UV_CACHE_DIR=/workspace/.uv/cache

ENV FNM_DIR=/workspace/.fnm
ENV FNM_NODE_DIST_MIRROR=https://nodejs.org/dist
ENV FNM_MULTISHELL_PATH=/workspace/.fnm
ENV PATH="/workspace/.fnm/aliases/default/bin:/usr/local/bin:/opt/user/.bin:${PATH}"

ENV BASH_ENV=/etc/profile.d/fnm.sh
ENV ENV=/etc/profile.d/fnm.sh

# Prebake Python 3.12 and Node 22 into /workspace for faster first site creation
USER frappe
RUN mkdir -p /workspace/.uv/python /workspace/.fnm && \
    echo "Prebaking Python ${DEFAULT_PYTHON_VERSION}..." && \
    uv python install ${DEFAULT_PYTHON_VERSION} && \
    PYTHON_PATH=$(uv python find ${DEFAULT_PYTHON_VERSION}) && \
    PYTHON_DIR=$(dirname $(dirname $PYTHON_PATH)) && \
    ln -sf python/$(basename $PYTHON_DIR) /workspace/.uv/python-default && \
    echo "Prebaking Node ${DEFAULT_NODE_VERSION}..." && \
    fnm install ${DEFAULT_NODE_VERSION} && \
    fnm default ${DEFAULT_NODE_VERSION} && \
    echo "Installing yarn globally..." && \
    npm install -g yarn

USER root
ENV WKHTMLTOPDF_VERSION=0.12.6.1-3
RUN if [ "$(uname -m)" = "aarch64" ]; then export ARCH=arm64; fi \
    && if [ "$(uname -m)" = "x86_64" ]; then export ARCH=amd64; fi \
    && downloaded_file=wkhtmltox_$WKHTMLTOPDF_VERSION.jammy_${ARCH}.deb \
    && wget -q https://github.com/wkhtmltopdf/packaging/releases/download/$WKHTMLTOPDF_VERSION/$downloaded_file \
    && dpkg -i $downloaded_file \
    && rm $downloaded_file

COPY --chmod=0755 ./entrypoint.sh /
COPY --chmod=0755 ./user-script.sh /scripts/
COPY --chmod=0755 ./launch_supervisor_service.sh /scripts/

WORKDIR /workspace

COPY --from=tooling --chown=frappe:frappe /opt/user /opt/user
COPY --from=tooling /opt/uv-tools /opt/uv-tools
COPY --from=tooling /etc/bash.bashrc /etc/bash.bashrc
COPY --from=tooling /etc/zsh/zshrc /etc/zsh/zshrc

# Setup fnm and python for interactive shells
RUN mkdir -p /etc/profile.d && \
    echo 'export FNM_DIR="/workspace/.fnm"' > /etc/profile.d/fnm.sh && \
    echo 'export FNM_MULTISHELL_PATH="/workspace/.fnm"' >> /etc/profile.d/fnm.sh && \
    echo 'eval "$(fnm env --shell bash 2>/dev/null || true)"' >> /etc/profile.d/fnm.sh && \
    echo 'if [ -d "/workspace/.uv/python-default/bin" ]; then' >> /etc/profile.d/fnm.sh && \
    echo '    export PATH="/workspace/.uv/python-default/bin:$PATH"' >> /etc/profile.d/fnm.sh && \
    echo 'fi' >> /etc/profile.d/fnm.sh && \
    chmod +x /etc/profile.d/fnm.sh && \
    echo '' >> /etc/bash.bashrc && \
    echo '# Source fnm and python setup for non-login shells' >> /etc/bash.bashrc && \
    echo 'if [ -f /etc/profile.d/fnm.sh ]; then' >> /etc/bash.bashrc && \
    echo '    . /etc/profile.d/fnm.sh' >> /etc/bash.bashrc && \
    echo 'fi' >> /etc/bash.bashrc && \
    echo '' >> /etc/zsh/zshrc && \
    echo '# Source fnm and python setup' >> /etc/zsh/zshrc && \
    echo 'if [ -f /etc/profile.d/fnm.sh ]; then' >> /etc/zsh/zshrc && \
    echo '    . /etc/profile.d/fnm.sh' >> /etc/zsh/zshrc && \
    echo 'fi' >> /etc/zsh/zshrc

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]
