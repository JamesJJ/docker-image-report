FROM alpine:3.8

ARG APP_CONFIG_BUILD_DATE
ENV APP_CONFIG_BUILD_DATE ${APP_CONFIG_BUILD_DATE:-unknown}
ARG APP_CONFIG_VERSION
ENV APP_CONFIG_VERSION ${APP_CONFIG_VERSION:-unknown}

#  -------- Begin: Include updates -------
RUN if test -f /etc/debian_version; then apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* ;fi
RUN if test -f /etc/alpine-release; then apk upgrade --no-cache -v && apk add --no-cache curl; fi
#  -------- End: Include updates -------

ENV DOCKERIZE_VERSION v0.6.0
RUN curl -sSfLO "https://github.com/jwilder/dockerize/releases/download/$DOCKERIZE_VERSION/dockerize-alpine-linux-amd64-$DOCKERIZE_VERSION.tar.gz" \
    && tar -C /usr/local/bin -xzvf "dockerize-alpine-linux-amd64-$DOCKERIZE_VERSION.tar.gz" \
    && rm "dockerize-alpine-linux-amd64-$DOCKERIZE_VERSION.tar.gz"

RUN adduser -h /opt -s /sbin/nologin -D -H -g app_daemon app_daemon

RUN apk add --no-cache \
      openssl \
      python3 \
      bash


RUN pip3 install --no-cache-dir --upgrade docker
RUN pip3 install --no-cache-dir --upgrade boto3
RUN pip3 install --no-cache-dir --upgrade markdown

# Create a "python" command, because some old scripts may expect it
RUN bash -c 'which python || ln -s "$(which python3)" "/usr/bin/python"'

RUN echo 'set -o vi' | tee -a /root/.bashrc

COPY . /opt/imagecheck

WORKDIR /opt/imagecheck

CMD \
  exec /usr/local/bin/dockerize \
    python3 bin/check.py




