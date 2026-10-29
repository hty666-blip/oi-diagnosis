# 判题镜像：在 go-judge 基础上补编译器与 Python
# 说明：go-judge 沙箱挂载的是“本容器”的 /usr、/lib，所以编译器必须装在同一个镜像里。
FROM criyle/go-judge:latest

# 构建期代理（本机需要经 Clash 才能访问外网）：
#   docker build --build-arg HTTPS_PROXY=http://host.docker.internal:7897 ...
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV http_proxy=$HTTP_PROXY https_proxy=$HTTPS_PROXY no_proxy=$NO_PROXY

USER root

RUN set -eux; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "60";\nAcquire::https::Timeout "60";\n' > /etc/apt/apt.conf.d/80-retries

RUN apt-get update -o Acquire::Check-Valid-Until=false \
 && apt-get install -y --no-install-recommends \
      g++ \
      python3 \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 运行期不需要代理
ENV http_proxy= https_proxy= no_proxy=

EXPOSE 5050
