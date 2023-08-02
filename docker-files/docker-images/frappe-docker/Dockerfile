FROM ubuntu:22.04 as bench

LABEL author=Xieyt
ARG PYTHON_VERSION=3.11.0

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
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
    # VSCode container requirements
    net-tools \
    # For pyenv build dependencies
    # https://github.com/frappe/frappe_docker/issues/840#issuecomment-1185206895
    make \
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
    gosu
    #&& rm -rf /var/lib/apt/lists/*

RUN sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && dpkg-reconfigure --frontend=noninteractive locales

# Detect arch and install wkhtmltopdf
ENV WKHTMLTOPDF_VERSION 0.12.6.1-3
RUN if [ "$(uname -m)" = "aarch64" ]; then export ARCH=arm64; fi \
    && if [ "$(uname -m)" = "x86_64" ]; then export ARCH=amd64; fi \
    && downloaded_file=wkhtmltox_$WKHTMLTOPDF_VERSION.jammy_${ARCH}.deb \
    && wget -q https://github.com/wkhtmltopdf/packaging/releases/download/$WKHTMLTOPDF_VERSION/$downloaded_file \
    && dpkg -i $downloaded_file \
    && rm $downloaded_file


# Install Python via pyenv
ENV PYENV_ROOT /opt/.pyenv
ENV PATH $PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH
ENV USERBASHRC /opt/user/.bashrc
ENV USERPROFILE /opt/user/.profile

# From https://github.com/pyenv/pyenv#basic-github-checkout
WORKDIR /opt

RUN mkdir -p /opt/user && touch /opt/user/.profile && touch /opt/user/.bashrc

RUN git clone --depth 1 https://github.com/pyenv/pyenv.git .pyenv \
    && pyenv install $PYTHON_VERSION \
    && PYENV_VERSION=$PYTHON_VERSION pip install --no-cache-dir virtualenv \
    && pyenv global $PYTHON_VERSION \
    && echo 'export PYENV_ROOT="/opt/.pyenv"' >> "$USERBASHRC" \
    && echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> "$USERBASHRC" \
    && echo 'eval "$(pyenv init -)"' >>"$USERBASHRC"

    #&& echo 'eval "$(pyenv init -)"' >>"$USERPROFILE"
    #&& sed -Ei -e '/^([^#]|$)/ {a export PYENV_ROOT="/opt/.pyenv" a export PATH="$PYENV_ROOT/bin:$PATH" a ' -e ':a' -e '$!{n;ba};}' "$USERPROFILE" \
    #&& echo 'eval "$(pyenv init --path)"' >>"$USERPROFILE" \

RUN pip install frappe-bench

# Install Node via nvm
ENV NODE_VERSION=18
ENV NVM_DIR /opt/.nvm
ENV PATH ${NVM_DIR}/versions/node/v${NODE_VERSION}/bin/:${PATH}


RUN mkdir -p /opt/.nvm \
    && wget -qO- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash \
    && . ${NVM_DIR}/nvm.sh \
    && nvm install ${NODE_VERSION} \
    && nvm use v${NODE_VERSION} \
    && npm install -g yarn \
    && nvm alias default v${NODE_VERSION} \
    && rm -rf ${NVM_DIR}/.cache \
    && echo 'export NVM_DIR="/opt/.nvm"' >>"$USERBASHRC" \
    && echo '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm' >> "$USERBASHRC" \
    && echo '[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion' >> "$USERBASHRC"

RUN mkdir -p /workspace

WORKDIR /workspace

RUN mkdir -p /opt/user/.bin
ENV PATH /opt/user/.bin:${PATH}

RUN echo PATH='/opt/user/.bin:$PATH' >> "$USERBASHRC"

COPY ./supervisord.conf /opt/user/
COPY ./bench-start.sh /opt/user/
COPY ./bench-wrapper.sh /opt/user/.bin/bench
COPY ./entrypoint.sh /
COPY ./user-script.sh /

RUN sudo chmod +x /entrypoint.sh /user-script.sh /opt/user/bench-start.sh /opt/user/.bin/bench

LABEL devcontainer.metadata='[{ "remoteUser": "frappe", "customizations": { "vscode": { "extensions": [ "ms-python.python", "ms-vscode.live-server", "grapecity.gc-excelviewer", "mtxr.sqltools", "visualstudioexptteam.vscodeintellicode"] } } }]'
#LABEL devcontainer.metadata='[{ \
#  "remoteUser": "frappe", \
#}]'

  #"extensions": [ "ms-python.python", "ms-vscode.live-server", "grapecity.gc-excelviewer", "mtxr.sqltools", "visualstudioexptteam.vscodeintellicode"], \

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]
