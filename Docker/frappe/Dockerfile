FROM ubuntu:22.04 AS bench

LABEL author=rtCamp
LABEL org.opencontainers.image.source=https://github.com/rtcamp/Frappe-Manager

ARG PYTHON_VERSIONS='3.12.0'
ARG PYENV_GIT_VERSION='2.6.7'
ARG PYENV_ROOT='/workspace/.pyenv'
ARG NVM_VERSION='0.39.0'
ARG NVM_DIR='/workspace/.nvm'
ARG NODE_VERSIONS='22.18.0'
ARG PREBAKE_APPS='erpnext:version-15,hrms:version-15'
ARG PREBAKE_FRAPPE_BRANCH='version-15'
SHELL ["/bin/bash", "-c"]

ENV PYTHON_VERSIONS=${PYTHON_VERSIONS}
ENV PYENV_GIT_VERSION=$PYENV_GIT_VERSION
ENV NVM_VERSION=${NVM_VERSION}
ENV NODE_VERSIONS=${NODE_VERSIONS}
ENV PYENV_ROOT=${PYENV_ROOT}
ENV NVM_DIR=${NVM_DIR}
ENV BENCH_USE_UV=true

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
    # For SSL certificates
    ca-certificates \
    # For frappe framework
    git \
    mariadb-client \
    python3-dev \
    python3-setuptools \
    python3-wheel \
    postgresql-client \
    gettext-base \
    wget \
    curl \
    rsync \
    # for PDF
    xfonts-75dpi \
    xfonts-base \
    libssl-dev \
    fonts-cantarell \
    libpangocairo-1.0-0 \
    # to work inside the container
    locales \
    cron \
    vim \
    watch \
    tree \
    iputils-ping \
    less \
    bash-completion \
    build-essential \
    # sudo \
    # nano \
    # software-properties-common \
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
    # VSCode container requirements
    net-tools \
    # For pyenv build dependencies
    # https://github.com/frappe/frappe_docker/issues/840#issuecomment-1185206895
    #make \
    # For pandas
    libbz2-dev \
    # For bench execute
    libsqlite3-dev \
    # For other dependencies
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
    && rm -rf /var/lib/apt/lists/*

RUN set -eux \
    && echo "en_US.UTF-8 UTF-8" > /etc/locale.gen \
    && dpkg-reconfigure --frontend=noninteractive locales \
    && update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# setup user
RUN export NAME='frappe' && \
    groupadd -g 1000 $NAME && \
    useradd --no-log-init -r -m -u 1000 -g 1000 -G sudo -s /usr/bin/zsh -d /workspace "$NAME" && \
    usermod -a -G tty "$NAME" && \
    echo "$NAME ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

RUN echo PREBAKE_APPS="$PREBAKE_APPS" >> /prebake_info && echo PREBAKE_FRAPPE_BRANCH="$PREBAKE_FRAPPE_BRANCH" >> /prebake_info

# ENV PATH=$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH

# for bench wrapper
ENV PATH=/opt/user/.bin:${PATH}

# for nvm
# ENV PATH=${NVM_DIR}/versions/node/v${NODE_VERSION}/bin/:${PATH}

COPY --chmod=0755 ./helper-function.sh /scripts/

RUN curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL=/usr/local/bin sh \
    && UV_PYTHON_INSTALL_DIR=/opt/user/uv uv python install $PYTHON_VERSION 3.11 \
    && UV_TOOL_BIN_DIR=/usr/local/bin UV_TOOL_DIR=/opt/user/uv-tools uv tool install frappe-bench && chmod 0755 /usr/local/bin/bench /opt/user/uv-tools \
    && rm -rf /root/.cargo /root/.cache/uv /root/.cache/pip \
    && mkdir -p /opt/user/.bin


COPY --chmod=0755 ./python_wrapper.sh /opt/user/.bin/python3

RUN uv_cpython_name="$(/bin/ls -1 /opt/user/uv)" && \
    uv_python_path="/opt/user/uv/$uv_cpython_name/bin" && \
    echo "UV_PYTHON_PATH=$uv_python_path" >> /etc/environment

ENV UV_PYTHON_PATH=${UV_PYTHON_PATH}
ENV UV_NATIVE_DEPS=1
ENV PATH=/opt/user/.bin:${UV_PYTHON_PATH}:${PATH}
RUN echo 'export PATH="/opt/user/.bin:${UV_PYTHON_PATH}:$PATH"' > /etc/profile.d/custom-path.sh && \
    chmod 0755 /etc/profile.d/custom-path.sh

FROM bench AS prebake

ENV PATH=$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH
ENV PATH="${NVM_DIR}/versions/node/${NODE_VERSIONS%% *}/bin/:${PATH}"

RUN chown -R frappe:frappe /opt

USER frappe

ENV USERZSHRC=/opt/user/.zshrc
ENV USERPROFILE=/opt/user/.profile

WORKDIR /opt

RUN mkdir -p /opt/user/conf.d && touch /opt/user/.profile && ls -lah

ENV ZSH=/workspace/.oh-my-zsh
ENV DISABLE_UPDATE_PROMPT=true

# install ohmyzsh
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
RUN unset ZSH

COPY --chown=frappe:frappe ./zshrc /opt/user/.zshrc
COPY --chown=frappe:frappe ./fm.zsh-theme /workspace/.oh-my-zsh/custom/themes/fm.zsh-theme
COPY --chown=frappe:frappe ./fmx /opt/user/fmx

RUN mkdir -p /workspace

USER root
RUN . /scripts/helper-function.sh && setup_pyenv_and_virtualenv ${PYENV_GIT_VERSION}
RUN echo 'export PATH="/opt/user/.bin:$PATH"' >> /etc/bash.bashrc && echo 'export PATH="/opt/user/.bin:$PATH"' >> /etc/zsh/zshrc
USER frappe

RUN mkdir -p /opt/user/.bin \
    && python3 -m venv /opt/user/bench-venv \
    && /opt/user/bench-venv/bin/pip install frappe-bench uv /opt/user/fmx \
    && rm -rf /opt/user/fmx \
    && chown -R frappe:frappe /opt/user/bench-venv \
    && ln -s /opt/user/bench-venv/bin/bench /opt/user/.bin/bench_orig \
    && ln -s /opt/user/bench-venv/bin/fmx /opt/user/.bin/fmx \
    && ln -s /opt/user/bench-venv/bin/uv /opt/user/.bin/uv

USER root
RUN . /scripts/helper-function.sh && setup_nvm_and_yarn "$NVM_VERSION"
USER frappe


WORKDIR /workspace

COPY --chown=frappe:frappe ./supervisord.conf /opt/user/
COPY --chown=frappe:frappe ./frappe-dev.conf /opt/user/
COPY --chown=frappe:frappe --chmod=0755 ./bench-dev-watch.sh /opt/user/
COPY --chown=frappe:frappe --chmod=0755 ./bench-dev-server /opt/user/
COPY --chown=frappe:frappe --chmod=0755 ./bench-wrapper.sh /opt/user/.bin/bench

COPY --chmod=0755 ./prebake.sh /scripts/

RUN ls -lah /workspace && (ls -lah /usr/local/bin | grep bench) && /scripts/prebake.sh && mv /workspace/frappe-bench/apps/* /workspace/

FROM ubuntu:22.04 AS prebake_image

WORKDIR /workspace

COPY --from=prebake --chown=frappe:frappe /workspace/frappe-bench /workspace/frappe-bench
COPY --from=prebake --chown=frappe:frappe /workspace/frappe /workspace/frappe-bench/apps/frappe
COPY --from=prebake --chown=frappe:frappe /workspace/erpnext /workspace/frappe-bench/apps/erpnext
COPY --from=prebake --chown=frappe:frappe /workspace/hrms /workspace/frappe-bench/apps/hrms

COPY --from=prebake --chown=frappe:frappe /workspace/.pyenv /workspace/.pyenv
COPY --from=prebake --chown=frappe:frappe /workspace/.nvm /workspace/.nvm
COPY --from=prebake --chown=frappe:frappe /workspace/.oh-my-zsh /workspace/.oh-my-zsh

FROM bench AS fm_image

ENV WKHTMLTOPDF_VERSION=0.12.6.1-3
RUN if [ "$(uname -m)" = "aarch64" ]; then export ARCH=arm64; fi \
    && if [ "$(uname -m)" = "x86_64" ]; then export ARCH=amd64; fi \
    && downloaded_file=wkhtmltox_$WKHTMLTOPDF_VERSION.jammy_${ARCH}.deb \
    && wget -q https://github.com/wkhtmltopdf/packaging/releases/download/$WKHTMLTOPDF_VERSION/$downloaded_file \
    && dpkg -i $downloaded_file \
    && rm $downloaded_file

RUN mkdir -p /scripts

COPY --chmod=0755 ./entrypoint.sh /
COPY --chmod=0755 ./user-script.sh /scripts/
COPY --chmod=0755 ./launch_supervisor_service.sh /scripts/
COPY --chmod=0755 ./launch_rqdash.sh /scripts/
COPY --chmod=0755 ./helper-function.sh /scripts/

RUN rm -rf /opt && mkdir -p /workspace /opt && rm -rf /etc/bash.bashrc /etc/zsh/zshrc

WORKDIR /workspace

# COPY --from=prebake --chown=frappe:frappe /opt/.pyenv /workspace/.pyenv
# COPY --from=prebake --chown=frappe:frappe /opt/.nvm /opt/.nvm
COPY --from=prebake --chown=frappe:frappe /opt/user /opt/user
COPY --from=prebake /etc/bash.bashrc /etc/bash.bashrc
COPY --from=prebake /etc/zsh/zshrc /etc/zsh/zshrc

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]
