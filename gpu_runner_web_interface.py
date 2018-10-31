from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from passlib.hash import sha256_crypt
from urllib.parse import quote_plus
from functools import wraps
import pickle
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import os
from getpass import getpass
from pymongo import MongoClient
from bson import ObjectId
from gpu_utils import get_gpus
from machine import Machine, _smi_command
from utils import get_abs_path

app = Flask(__name__)


def is_logged_in(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if session.get("username"):
            return func(*args, **kwargs)
        else:
            return redirect(url_for("login"))

    return wrapper


@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        json = request.get_json()
        username = json["username"]
        password = json["password"]

        with open(get_abs_path(__file__, "passwords"), "rb") as f:
            passwords = pickle.load(f)

        if username in passwords and sha256_crypt.verify(password, passwords[username]):
            session["username"] = username
            return jsonify({"url": url_for("dashboard")})
        else:
            # TODO: msg... bad username or password
            return jsonify({"url": ""})
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@is_logged_in
def dashboard():
    if request.method == "POST":
        json = request.get_json()
        action = json["action"]

        if action == "add":
            gpu_runner_db.jobs.insert_many(json["commands"])
        else:
            assert action == "delete"
            delete_ids = [ObjectId(_id) for _id in json["_ids"]]
            gpu_runner_db.jobs.remove({"_id": {"$in": delete_ids}})
        return ""
    else:
        return render_template("dashboard.html")


@app.route("/add_machine", methods=["GET", "POST"])
@is_logged_in
def add_machine():
    if request.method == "POST":
        json = request.get_json()
        action = json.pop("action")

        if action == "add":
            gpu_runner_db.machines.insert_one(json)

            # add to current machines / connections
            machine = Machine(
                app=app, jobs_db=jobs_db, ssh_password=ssh_password, **json
            )
            machine.start()
            machines.update({json["_id"]: machine})
        else:
            assert action == "delete"
            delete_ids = [machine["_id"] for machine in json["machines"]]
            gpu_runner_db.machines.remove({"_id": {"$in": delete_ids}})

            # also remove these machines from the current list of machines / connections
            for machine in json["machines"]:
                machines.pop(machine["_id"])
        return ""
    else:
        return render_template("add_machine.html")


@app.route("/toggle_gpu_runner", methods=["POST"])
@is_logged_in
def toggle_gpu_runner():
    json = request.get_json()
    machines[json["machine"]].gpu_runner_on = json["gpu_runner_on"]
    return ""


@app.route("/data/gpus")
@is_logged_in
def data_gpus():
    try:
        gpus = {
            machine._id: [
                gpu._asdict()
                for gpu in get_gpus(info_string=machine.execute(_smi_command))
            ]
            for machine in machines.values()
        }
    except FileNotFoundError:  # nvidia-smi not found
        gpus = {}
    return jsonify(gpus)


# POST because we need to send data (the machine name)
@app.route("/data/jobs", methods=["POST"])
@is_logged_in
def data_jobs():
    json = request.get_json()["machine"]
    jobs = list(gpu_runner_db.jobs.find({"machine": json["_id"]}))
    for job in jobs:
        job["_id"] = str(job["_id"])  # convert object id to string so we can jsonify
    return jsonify(jobs)


@app.route("/data/machines")
@is_logged_in
def data_machines():
    return jsonify([machine.dashboard_data() for machine in machines.values()])


def first_time_setup(key_fname, passwords_fname):
    if not os.path.isfile(key_fname):
        from os import urandom, chmod

        with open(key_fname, "wb") as f:
            f.write(urandom(50))
        chmod(key_fname, 0o600)

    if not os.path.isfile(passwords_fname):
        from os import urandom, chmod

        username = input("Create a username: ")
        while True:
            password = getpass("Create a password: ")
            password_confirmation = getpass("Confirm your password: ")
            if password == password_confirmation:
                break
            else:
                print("Passwords didn't match! Try again.")

        with open(passwords_fname, "wb") as f:
            pickle.dump({username: sha256_crypt.encrypt(password)}, f)
            del password
            del password_confirmation
        chmod(passwords_fname, 0o600)


if __name__ == "__main__":
    parser = ArgumentParser(description="", formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument(
        "-p",
        "--port",
        help="Which port to run on [default = 5000]",
        default=5000,
        type=int,
    )
    parser.add_argument(
        "-d", "--debug", help="Flag: whether to turn on debug mode", action="store_true"
    )
    parser.add_argument("-l", "--log_level", help="[default = ERROR]", default="ERROR")

    args = parser.parse_args()
    app.logger.setLevel(args.log_level.upper())
    app.config.update({"DEBUG": args.debug, "TEMPLATES_AUTO_RELOAD": True})

    key_fname = get_abs_path(__file__, "flask_key")
    passwords_fname = get_abs_path(__file__, "passwords")

    first_time_setup(key_fname, passwords_fname)

    ssh_password = getpass("Enter your SSH password: ")

    with open(key_fname, "rb") as f:
        app.secret_key = f.read()

    user = "web_runner"
    db_password = getpass("MongoDB web_runner password: ")
    mongo_client = MongoClient(
        f"mongodb://{quote_plus(user)}:{quote_plus(db_password)}@localhost/?authSource=gpu_runner"
    )
    del db_password

    gpu_runner_db = mongo_client.gpu_runner
    jobs_db = gpu_runner_db.jobs

    machines = {}
    for machine in gpu_runner_db.machines.find():
        try:
            machines[machine["_id"]] = Machine(
                app=app, jobs_db=jobs_db, ssh_password=ssh_password, **machine
            )
            app.logger.info(f"Established connection to {machine['_id']}")
        except:
            app.logger.error(f"Error establishing connection to {machine['_id']}")

    for machine in machines.values():
        machine.start()

    app.run(port=args.port)
