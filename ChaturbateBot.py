# -*- coding: utf-8 -*-
import telebot
import os
import time
import urllib.request
import os.path
import argparse
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from requests_futures.sessions import FuturesSession
import json

ap = argparse.ArgumentParser()
ap.add_argument(
    "-k", "--key", required=True, type=str, help="Telegram bot key")
ap.add_argument(
    "-f",
    "--working-folder",
    required=False,
    type=str,
    default=os.getcwd(),
    help="set the bot's working-folder")
ap.add_argument(
    "-t",
    "--time",
    required=False,
    type=float,
    default=0.2,
    help="time wait between every connection made, in seconds")
ap.add_argument(
    "-threads",
    required=False,
    type=int,
    default=10,
    help="The number of multiple http connection opened at the same to check chaturbate"
)
ap.add_argument(
    "-l",
    "--limit",
    required=False,
    type=int,
    default=0,
    help="The maximum number of multiple users a person can follow")
ap.add_argument(
    "-sentry", required=False, type=str, default="", help="Your sentry personal url")
args = vars(ap.parse_args())
bot = telebot.TeleBot(args["key"])
bot_path = args["working_folder"]
wait_time = args["time"]
sentry_key = args["sentry"]
http_threads = args["threads"]
user_limit = args["limit"]
if sentry_key != "":
    import sentry_sdk
    sentry_sdk.init(sentry_key)

    def handle_exception(e):
        sentry_sdk.capture_exception()
else:

    def handle_exception(e):
        print(str(e))


def risposta(sender, messaggio):
    try:
        bot.send_chat_action(sender, action="typing")
        bot.send_message(sender, messaggio)
    except Exception as e:
        handle_exception(e)


def risposta_html(sender, messaggio):
    try:
        bot.send_chat_action(sender, action="typing")
        bot.send_message(sender, messaggio, parse_mode="HTML")
    except Exception as e:
        handle_exception(e)


def exec_query(query):
    # Open database connection
    db = sqlite3.connect(bot_path + '/database.db')
    # prepare a cursor object using cursor() method
    cursor = db.cursor()
    # Prepare SQL query to INSERT a record into the database.
    try:
        # Execute the SQL command
        cursor.execute(query)
        # Commit your changes in the database
        db.commit()
    except Exception as e:
        # Rollback in case there is any error
        handle_exception(e)
        db.rollback()
    # disconnect from server
    db.close()


# default table creation
exec_query("""CREATE TABLE IF NOT EXISTS CHATURBATE (
        USERNAME  CHAR(60) NOT NULL,
        CHAT_ID  CHAR(100),
        ONLINE CHAR(1))""")


def check_online_status():
    while (1):
        username_list = []
        chatid_list = []
        online_list = []
        response_list = []
        sql = "SELECT * FROM CHATURBATE"
        try:
            db = sqlite3.connect(bot_path + '/database.db')
            cursor = db.cursor()
            cursor.execute(sql)
            results = cursor.fetchall()
            for row in results:
                username_list.append(row[0])
                chatid_list.append(row[1])
                online_list.append(row[2])
        except Exception as e:
            handle_exception(e)
        finally:
            db.close()
        session = FuturesSession(
            executor=ThreadPoolExecutor(max_workers=http_threads))
        for x in range(0, len(username_list)):
            try:
                response = ((session.get(
                    "https://it.chaturbate.com/api/chatvideocontext/" +
                    username_list[x].lower())).result()).content  # lowercase to fix old entries in db+ more safety
            except Exception as e:
                handle_exception(e)
                response = "error"
            response_list.append(response)
            time.sleep(wait_time)
        for x in range(0, len(response_list)):
            try:
                if ("status" not in json.loads(response_list[x]) and response != "error"):
                    if (json.loads(response_list[x])["room_status"] == "offline"):
                        if online_list[x] == "T":
                            exec_query("UPDATE CHATURBATE \
                    SET ONLINE='{}'\
                    WHERE USERNAME='{}' AND CHAT_ID='{}'".format(
                                "F", username_list[x], chatid_list[x]))
                            risposta(chatid_list[x],
                                     username_list[x] + " is now offline")
                    elif (online_list[x] == "F"):
                        risposta(
                            chatid_list[x], username_list[x] +
                            " is now online! You can watch the live here: http://en.chaturbate.com/"
                            + username_list[x])
                        exec_query("UPDATE CHATURBATE \
                SET ONLINE='{}'\
                WHERE USERNAME='{}' AND CHAT_ID='{}'".format(
                            "T", username_list[x], chatid_list[x]))
                elif response != "error":
                    response = json.loads(response_list[x])['status']
                    if "401" in response:
                        exec_query("DELETE FROM CHATURBATE \
                     WHERE USERNAME='{}'".format(username_list[x]))
                        risposta(chatid_list[x], username_list[x] +
                                 " has been removed from your followed usernames because it was banned")
                        print(
                            username_list[x], "has been removed because it was banned")
            except Exception as e:
                handle_exception(e)


def telegram_bot():
    @bot.message_handler(commands=['start', 'help'])
    def handle_start_help(message):
        risposta(
            message.chat.id,
            "/add username to add an username to check \n/remove username to remove an username (you can use /remove all to remove all models at once) \n/list to see which users you are currently following"
        )

    @bot.message_handler(commands=['add'])
    def handle_add(message):
        print("add")
        chatid = message.chat.id
        try:
            if len(message.text.split(" ")) < 2:
                risposta(
                    chatid,
                    "You may have made a mistake, check your input and try again"
                )
                return
            # not lowercase usernames bug the api calls
            username = message.text.split(" ")[1].lower()
        except Exception as e:
            risposta(chatid, "An error happened, try again")
            handle_exception(e)
            return
        try:
            target = "http://it.chaturbate.com/" + username
            req = urllib.request.Request(
                target, headers={'User-Agent': 'Mozilla/5.0'})
            html = urllib.request.urlopen(req).read()
            if (b"Access Denied. This room has been banned.</span>" in html
                    or username == ""):
                risposta(
                    chatid, username +
                    " was not added because it doesn't exist or it has been banned. If you are sure it exists, you may want to try the command again"
                )
            else:
                username_list = []
                db = sqlite3.connect(bot_path + '/database.db')
                cursor = db.cursor()
                sql = "SELECT * FROM CHATURBATE \
          WHERE CHAT_ID='{}'".format(chatid)
                try:
                    cursor.execute(sql)
                    results = cursor.fetchall()
                    for row in results:
                        username_list.append(row[0])
                except Exception as e:
                    handle_exception(e)
                finally:
                    db.close()
                if len(username_list) < user_limit or user_limit == 0:
                    if username not in username_list:
                        exec_query("INSERT INTO CHATURBATE \
            VALUES ('{}', '{}', '{}')".format(username, chatid, "F"))
                        risposta(chatid, username + " has been added")
                    else:
                        risposta(chatid,
                                 username + " has already been added")
                else:
                    risposta(
                        chatid,
                        "You have reached your maximum number of permitted followed models, which is "
                        + str(user_limit))
        except Exception as e:
            handle_exception(e)
            risposta(
                chatid, username +
                " was not added because it doesn't exist or it has been banned"
            )

    @bot.message_handler(commands=['remove'])
    def handle_remove(message):
        print("remove")
        chatid = message.chat.id
        username_list = []
        if len(message.text.split(" ")) < 2:
            risposta(
                chatid,
                "You may have made a mistake, check your input and try again")
            return
        username = message.text.split(" ")[1]
        if username == "":
            risposta(
                chatid,
                "The username you tried to remove doesn't exist or there has been an error"
            )
            return

        sql = "SELECT * FROM CHATURBATE WHERE USERNAME='{}' AND CHAT_ID='{}'".format(
            username, chatid)
        try:
            db = sqlite3.connect(bot_path + '/database.db')
            cursor = db.cursor()
            cursor.execute(sql)
            results = cursor.fetchall()
            for row in results:
                username_list.append(row[0])
        except Exception as e:
            handle_exception(e)
        finally:
            db.close()

        if username == "all":
            exec_query("DELETE FROM CHATURBATE \
        WHERE CHAT_ID='{}'".format(chatid))
            risposta(chatid, "All usernames have been removed")

        elif username in username_list:  # this could have a better implementation but it works
            exec_query("DELETE FROM CHATURBATE \
        WHERE USERNAME='{}' AND CHAT_ID='{}'".format(username, chatid))
            risposta(chatid, username + " has been removed")

        else:
            risposta(
                chatid,
                "You aren't following the username you have tried to remove")

    @bot.message_handler(commands=['list'])
    def handle_list(message):
        chatid = message.chat.id
        username_list = []
        online_list = []
        followed_users = ""
        db = sqlite3.connect(bot_path + '/database.db')
        cursor = db.cursor()
        sql = "SELECT * FROM CHATURBATE \
        WHERE CHAT_ID='{}'".format(chatid)
        try:
            cursor.execute(sql)
            results = cursor.fetchall()
            for row in results:
                username_list.append(row[0])
                online_list.append(row[2])
        except Exception as e:
            handle_exception(e)
        else:  # else means that the code will get executed if an exception doesn't happen
            for x in range(0, len(username_list)):
                followed_users += username_list[x] + ": "
                if online_list[x] == "T":
                    followed_users += "<b>online</b>\n"
                else:
                    followed_users += "offline\n"
        finally:
            db.close()
        if followed_users == "":
            risposta(chatid, "You aren't following any user")
        else:
            risposta_html(chatid,
                          "These are the users you are currently following:\n"
                          + followed_users)

    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            handle_exception(e)


threads = []
check_online_status_thread = threading.Thread(target=check_online_status)
telegram_bot_thread = threading.Thread(target=telegram_bot, daemon=True)
threads.append(check_online_status_thread)
threads.append(telegram_bot_thread)
check_online_status_thread.start()
telegram_bot_thread.start()
