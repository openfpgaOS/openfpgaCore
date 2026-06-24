# openfpgaOS — Quartus build container.
#
# Quartus Prime itself is NOT baked in: it is bind-mounted read-only from the
# host at run time (-v /home/alberto/altera_lite:...:ro), which keeps this image
# small and sidesteps redistribution.  This image provides only the system libs
# Quartus's command-line tools (quartus_map/fit/asm/sta) link against, plus a
# UTF-8 locale for its Tcl shell.  Each container gets its own $HOME and /tmp,
# which is what lets N builds (any variant, any seed) run concurrently without
# the shared-state segfault that plagues concurrent Quartus on one host.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libsm6 libice6 libxext6 libxrender1 libxft2 libxt6 \
      libxmu6 libxi6 libxtst6 libxfixes3 libxcursor1 libxinerama1 \
      libxrandr2 libxdamage1 libxcomposite1 libfontconfig1 libfreetype6 \
      libgl1 libglu1-mesa zlib1g libpng16-16 \
      locales make \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
