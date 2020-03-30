# Build from the official Python 3 Docker image
FROM python:3

# I am the maintainer - Dylan Kauling
MAINTAINER gunsmithy@gmail.com

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python", "./Pogsmithy-Twitch.py" ]
