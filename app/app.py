import os
import logging
from collections import defaultdict
import hmac
from flask import (abort, Flask, session, render_template, 
                   session, redirect, url_for, request,
                   flash, jsonify)
from flask_session import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from mlapp import GitHubApp
from tensorflow.keras.models import load_model
from tensorflow.keras.utils import get_file
from utils import IssueLabeler
import dill as dpickle
from urllib.request import urlopen
from sql_models import db, Issues, Predictions
import tensorflow as tf
import ipdb

app = Flask(__name__)

# Configure session to use filesystem. Hamel: BOILERPLATE.
app.config["SESSION_PERMANENT"] = False
Session(app)

# Bind database to flask app
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

# Additional Setup inspired by https://github.com/bradshjg/flask-githubapp/blob/master/flask_githubapp/core.py
app.webhook_secret = os.getenv('WEBHOOK_SECRET')
LOG = logging.getLogger(__name__)    

# set the prediction threshold at .6 for everything except for the label question which has higher threshold of .7
prediction_threshold = defaultdict(lambda: .6)
prediction_threshold['question'] = .7


def init():
    "Load all necessary artifacts to make predictions."
    title_pp_url = "https://storage.googleapis.com/codenet/issue_labels/issue_label_model_files/title_pp.dpkl"
    body_pp_url = 'https://storage.googleapis.com/codenet/issue_labels/issue_label_model_files/body_pp.dpkl'
    model_url = 'https://storage.googleapis.com/codenet/issue_labels/issue_label_model_files/Issue_Label_v1_best_model.hdf5'
    model_filename = 'downloaded_model.hdf5'

    #save keyfile
    pem_string = os.getenv('PRIVATE_KEY')
    if not pem_string:
        raise ValueError('Environment variable PRIVATE_KEY was not supplied.')
    
    with open('private-key.pem', 'wb') as f:
        f.write(str.encode(pem_string))

    with urlopen(title_pp_url) as f:
        title_pp = dpickle.load(f)

    with urlopen(body_pp_url) as f:
        body_pp = dpickle.load(f)
    
    model_path = get_file(fname=model_filename, origin=model_url)
    model = load_model(model_path)
    app.graph = tf.get_default_graph()
    app.issue_labeler = IssueLabeler(body_text_preprocessor=body_pp,
                                     title_text_preprocessor=title_pp,
                                     model=model)


# smee by default sends things to /event_handler route
@app.route("/event_handler", methods=["POST"])
def index():
    "Handle payload"
    # authenticate webhook to make sure it is from GitHub
    verify_webhook(request)

    # if user tries to login, try to authenticate them using naive approach.
    payload = request.json

    # Check if payload corresponds to an issue being opened
    if request.json['action'] == 'opened' and ('issue' in request.json):
        # get metadata
        installation_id = request.json['installation']['id']
        issue_num = request.json['issue']['number']
        username, repo = request.json['repository']['full_name'].split('/')
        title = request.json['issue']['title']
        body = request.json['issue']['body']

        # write the issue to the database using ORM
        issue_db_obj = Issues(repo=repo,
                              username=username,
                              issue_num=issue_num,
                              title=title,
                              body=body)
        
        db.session.add(issue_db_obj)
        db.session.commit()
        
        # make predictions with the model
        with app.graph.as_default():
            predictions = app.issue_labeler.get_probabilities(body=body, title=title)
        #log to console
        print(f'issue opened by {username} in {repo} #{issue_num}: {title} \nbody:\n {body}\n')
        print(f'predictions: {str(predictions)}')

        # get the most confident prediction
        argmax = max(predictions, key=predictions.get)
        # take an action if the prediction is confident enough
        if predictions and (predictions[argmax] >= prediction_threshold[argmax]):
            # create message
            message = f'KFlow-bot has determined with {predictions[argmax]:.2f} probability that this issue should be labeled as a `{argmax}` and is auto-labeling this issue. Please mark this comment with :thumbsup: or :thumbsdown: to give our bot feedback!'
            # label the issue and make a comment using the GitHub api
            issue = get_issue_handle(installation_id, username, repo, issue_num)
            comment = issue.create_comment(message)
            issue.add_labels(argmax)

            # log the prediction to the database using ORM
            issue_db_obj.add_prediction(comment_id=comment.id,
                                        prediction=argmax,
                                        probability=predictions[argmax],
                                        logs=str(predictions))

    else:
        pass
    return 'ok'

@app.route("/data/<string:owner>/<string:repo>")
def data(owner, repo):
    issues = Issues.query.filter(Issues.username == owner, Issues.repo == repo).all()
    issue_numbers = [x.issue_id for x in issues]

    predictions = Predictions.query.filter(Predictions.issue_id.in_(issue_numbers)).all()

    return render_template("data.html",
                           results=predictions,
                           owner=owner,
                           repo=repo)


@app.route("/update_feedback/<string:owner>/<string:repo>")
def update_feedback(owner, repo):
    "Update feedback for predicted labels for an owner/repo"
    # authenticate webhook to make sure it is from GitHub
    issues = Issues.query.filter(Issues.username == owner, Issues.repo == repo).all()
    issue_numbers = [x.issue_id for x in issues]

    predictions = Predictions.query.filter(Predictions.issue_id.in_(issue_numbers)).all()

    # grab all the reactions and update the statistics in the database.
    for prediction in predictions:
        reactions = get_reactions(owner=owner, repo=repo, comment_id=prediction.comment_id)
        prediction.likes = reactions['+1']
        prediction.dislikes = reactions['-1']
    db.session.commit()
    print(f'Successfully updated feedback based on reactions for {len(predictions)} predictions in {owner}/{repo}.')
    return redirect(url_for('data'))

def get_app():
    "grab a fresh instance of the app handle."
    app_id = 27079
    key_file_path = 'private-key.pem'
    ghapp = GitHubApp(pem_path=key_file_path, app_id=app_id)
    return ghapp

def get_issue_handle(installation_id, username, repository, number):
    "get an issue object."
    ghapp = get_app()
    install = ghapp.get_installation(installation_id)
    return install.issue(username, repository, number)

def get_reactions(owner, repo, comment_id):
    "get all reactions for an issue comment."
    ghapp = get_app()
    return ghapp.get_reactions(owner=owner, repo=repo, comment_id=comment_id)

def verify_webhook(request):
    "Make sure request is from GitHub.com"
    # Inspired by https://github.com/bradshjg/flask-githubapp/blob/master/flask_githubapp/core.py#L191-L198
    signature = request.headers['X-Hub-Signature'].split('=')[1]

    mac = hmac.new(str.encode(app.webhook_secret), msg=request.data, digestmod='sha1')

    if not hmac.compare_digest(mac.hexdigest(), signature):
        LOG.warning('GitHub hook signature verification failed.')
        abort(400)


if __name__ == "__main__":
    init()
    with app.app_context():
        # create tables if they do not exist
        db.create_all()

    # make sure things reload
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(debug=True, host='0.0.0.0', port=os.getenv('PORT'))