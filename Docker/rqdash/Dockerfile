FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
		gcc \
		git \ 
		libc-dev \
	&& rm -rf /var/lib/apt/lists/*

RUN pip3 install git+https://github.com/Parallels/rq-dashboard.git@v0.8.2

ENTRYPOINT ["python3", "-m", "rq_dashboard"]
