# Overview

GPU Dashboard is a simple webpage for running commands on different machines. The machine running the server will SSH into different machines for you (after you save the required username and password) and run commands that you enter through the webpage client. The webpage also shows basic GPU information - memory and utilization used.

The main features are
* observe GPU usage on multiple machines at once
* queue many jobs at once and have them start as resources become available
* support for queueing many commands which are slight variants of the form you could construct with a for loop (e.g. `do_something -n 0`, `do_something -n 1`, ...)

## Command Format
Commands should
* include a single placeholder `{}` which will be filled with the id of the GPU to use
* be a single-line string (no newlines)
* not include `&` to background the command; this is automatically included for all commands
* not redirect standout output/error; these are both directed to append to `~/.gpu_log`

## Caveats
* The same SSH password has to be used to connect to all machines
  * Only username + password authentication is supported
* If the server goes down, no more queued jobs will be started until it's running again (i.e. there's no transfer of the job queue to the individual machines or some way that they can keep running the next jobs without the server's supervision)
  * If serving on your own computer, you'll have to keep it on and connected to the internet whenever you want new jobs to keep starting

# Startup

(see below for details on [installation](#installation) and [first time setup](#setup))

```bash
mongod --auth --dbpath mongodb --bind_ip 127.0.0.1
```

```bash
python gpu_runner_web_interface.py
```

# Installation

## GPU Dashboard

TODO

## MongoDB

Follow the instructions [here][mongo_install] to install MongoDB. In short:

```bash
# add MongoDB GPG key
sudo apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv 9DA31620334BD75D9DCB49F368818C72E52529D4
echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu xenial/mongodb-org/4.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-4.0.list
sudo apt-get update
sudo apt-get install -y mongodb-org # latest version
```

Make sure to also set up whatever directories you'll use to store you databases.

[mongo_install]: https://docs.mongodb.com/manual/tutorial/install-mongodb-on-ubuntu/
[auth]: https://docs.mongodb.com/manual/tutorial/enable-authentication/

# Setup

## Mongo Authentication
Set up authentication for your database as shown [here][auth]. You'll probably want to make both a `userAdminAnyDatabase` user as well as the required user `web_runner` with `readWrite` access to the `gpu_runner` database. Here's an example of creating the required users:

```bash
mongod --dbpath mongodb # start mongo server
```

(in a separate terminal)

```bash
mongo # connect shell to mongo server

use admin
db.createUser({
    user: "admin",
    pwd: "password",
    roles: [{ role: "userAdminAnyDatabase", db: "admin" }]
})

use gpu_runner
db.createUser({
    user: 'web_runner',
    pwd: 'password',
    roles: [{ role: 'readWrite', db: 'gpu_runner' }]
})
exit
```

The password that you create for `web_runner` is the one that you'll need to enter every time that you start the dashboard server so that it has access to this database. The `gpu_runner` database will be used to store information about the different machines as well as the list of jobs that are queued.

Now that you have a `userAdminAnyDatabase` configured, make sure to always run Mongo with authentication enabled (e.g. `mongod --auth --dbpath mongodb`).

## GPU Dashboard

The first time that you start the web server, it will prompt you to create a username and password. These will be used to log in to the web client. There's no way to recover them if forgotten, but you can remove the current username + password by deleting the `passwords` file stored in the installation directory.

# Security
Anybody who can access the logged-in client can execute arbitrary code as you on any of the machines that you've set up.

* It's expected that you are the sole user of your server/client and, if you're concerned about security, that you run the server and access the client on your own computer.
* Your SSH password is never stored to disk; you have to enter it again each time you start the server. It is kept in memory while GPU Dashboard is running.
* The password to the `web_runner` database is never stored by GPU Dashboard; you have to enter it again each time you start the server.
* The password to log in to the client is never stored; the username and a hash of the password are stored locally.
