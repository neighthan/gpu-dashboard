
# Setup

## MongoDB
Follow the instructions [here][mongo_sudo] to install Mongo with `sudo` or [here][mongo] without `sudo`. In short:

```bash
curl -O https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-3.4.10.tgz
tar -zxvf mongodb-linux-x86_64-3.4.10.tgz
mkdir -p mongodb
cp -R mongodb-linux-x86_64-3.4.10/* mongodb

mkdir mongodb/data # or wherever you want to store your data
```

Make sure to add `mongodb/bin` to your `PATH`.


Then setup authentication for your database as shown [here][auth]. You'll probably want to make both a `userAdminAnyDatabase` user as well as the required user `web_runner` with `readWrite` access to the `gpu_runner` database. Here's an example of creating the required users:

```
mongod --dbpath mongodb/data # start mongo console

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
```

The password that you create for `web_runner` is the one that you'll need to enter every time that you start the dashboard so that it has access to this database.

[mongo_sudo]: https://docs.mongodb.com/manual/tutorial/install-mongodb-on-ubuntu/
[mongo]: https://docs.mongodb.com/manual/tutorial/install-mongodb-on-linux/
[auth]: https://docs.mongodb.com/manual/tutorial/enable-authentication/
