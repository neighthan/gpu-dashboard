from pymongo import MongoClient
from getpass import getpass
from argparse import ArgumentParser
import os
from subprocess import run


def main():
    parser = ArgumentParser()
    parser.add_argument("port", type=int, default=27010)
    parser.add_argument(
        "mongo_url",
        default="https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu1604-4.0.3.tgz",
        help="Update this when a new version is available; check https://www.mongodb.com/download-center/community.",
    )
    parser.add_argument("install_dir", default="", help="[default = $HOME/mongo]")
    parser.add_argument("db_dir", default="", help="[default = $install_dir/db]")

    args = parser.parse_args()
    install_dir = args.install_dir or os.environ["HOME"] + "/mongo"
    db_dir = args.db_dir or install_dir + "/db"

    install_mongo(args.mongo_url, install_dir, db_dir)
    setup_users(db_dir, args.port)

    suggestions = f"""
    Make sure to add {install_dir}/bin to your path! e.g. run
    `echo 'export PATH=\"$PATH:{install_dir}/bin\"' | cat >> ~/.bash_profile`
    We recommend also adding an alias command to your .bash_profile to start mongo, e.g.
    alias start_mongo='mongod --auth --dbpath {db_dir} --port {args.port} --bind_ip 127.0.0.1 --directoryperdb --journal --noprealloc --smallfiles'
    (all of the flags after bind_ip can be removed; this is just what I use)
    """

    print(suggestions)


def install_mongo(mongo_url: str, install_dir: str, db_dir: str):
    os.makedirs(install_dir, exist_ok=True)
    os.makedirs(db_dir, exist_ok=True)

    mongo_dir = mongo_url.replace(".tgz", "")
    commands = [
        f"wget {mongo_url}",
        f"tar -xzf {mongo_url}",
        f"rm {mongo_url}",
        f"mv {mongo_dir}/* {install_dir}",
        f"rm -r {mongo_dir}",
    ]

    for command in commands:
        run(command.split(" "))


def setup_users(db_dir: str, port: int):
    input(
        f"Run `mongod --dbpath {db_dir}` --port {port} --directoryperdb --journal --noprealloc --smallfiles`"
        "in another window, then press enter. (you can ignore all of the flags after port; this is just what I use.)"
    )

    admin_pwd = getpass("Enter admin password:")
    web_runner_pwd = getpass("Enter web runner password:")

    client = MongoClient(port=port)
    client.admin.add_user(
        "admin", admin_pwd, roles=[{"role": "userAdminAnyDatabase", "db": "admin"}]
    )
    client.gpu_runner.add_user(
        "web_runner", web_runner_pwd, roles=[{"role": "readWrite", "db": "gpu_runner"}]
    )
    print("Successfully created admin and web_runner roles!")
    print(
        "Make sure to kill the running `mongod` process. Always start it with `--auth` from now on!"
    )


if __name__ == "__main__":
    main()
