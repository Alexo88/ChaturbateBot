# -*- coding: utf-8 -*-

import datetime
import json
import logging
import os
import os.path
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from queue import Queue
from pathlib import Path

import requests
import telegram
from PIL import Image
from requests_futures.sessions import FuturesSession
from telegram.error import (BadRequest, ChatMigrated, NetworkError,
                            TelegramError, TimedOut, Unauthorized)
from telegram.ext import CommandHandler, Updater

from modules import Preferences
from modules import Utils
from modules.Argparse_chaturbatebot import args

updater = Updater(token=args["key"])
dispatcher = updater.dispatcher

bot_path = args["working_folder"]
wait_time = args["time"]
http_threads = args["threads"]
user_limit = args["limit"]
auto_remove = Utils.str2bool(args["remove"])
admin_pw = args["admin_password"]
logging_file=args["logging_file"]

logging_level=logging.INFO
if not Utils.str2bool(args["enable_logging"]):
    logging_level=99 # stupid workaround not to log -> only creates file

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging_level,filename=logging_file)


def send_message(chatid: str, messaggio: str, bot, html: bool=False) -> None:
    """
    Sends a message to a telegram user

    :param str chatid: The chatid of the user who will receive the message
    :param str messaggio: The message who the user will receive
    :param bot: telegram bot instance
    :param bool html: Enable html markdown parsing in the message
    """

    disable_webpage_preview= not Preferences.get_user_link_preview_preference(chatid) #the setting is opposite of preference

    try:
        bot.send_chat_action(chat_id=chatid, action="typing")
        if html:
            bot.send_message(chat_id=chatid, text=messaggio,
                             parse_mode=telegram.ParseMode.HTML,disable_web_page_preview=disable_webpage_preview)
        else:
            bot.send_message(chat_id=chatid, text=messaggio,disable_web_page_preview=disable_webpage_preview)
    except Unauthorized: #user blocked the bot
        if auto_remove == True:
            logging.info(f"{chatid} blocked the bot, he's been removed from the database")
            Utils.exec_query(f"DELETE FROM CHATURBATE WHERE CHAT_ID='{chatid}'")
            Preferences.remove_user_from_preferences(chatid)
    except Exception as e:
        Utils.handle_exception(e)





#region normal functions


def start(bot, update) -> None:
    send_message(
        update.message.chat.id,
        "/add username to add an username to check \n/remove username to remove an username\n(you can use /remove <b>all</b> to remove all models at once) \n/list to see which users you are currently following", bot, html=True
    )

def is_model_viewable(model: str) -> bool:
    """
    Checks if you can see the model live (without any privilege)

    :param string model: The model's name
    :rtype: bool
    """
    target = f"https://en.chaturbate.com/api/chatvideocontext/{model}"
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.110 Safari/537.36', }
    response = requests.get(target, headers=headers)

    if b"It's probably just a broken link, or perhaps a cancelled broadcaster." in response.content: #check if models still exists
        return False
    else:
        response = json.loads(response.content)

    if ("status" not in response and response != "error"):
        if (response["room_status"] == "offline" or response["room_status"] == "away" or response["room_status"] == "private" or response["room_status"]=="hidden"):
            return False
    elif ("status" in response): #avoid keyerror
        if response["status"]==401: 
            return False
    elif response == "error":
        return False
    return True

def add(bot, update, args) -> None:
    chatid = update.message.chat_id
    username_message_list=[]
    if len(args) < 1:
            send_message(
                chatid,
                "You need to specify an username to follow, use the command like /add <b>username</b>\n You can also add multiple users at the same time by separating them using a comma, like /add <b>username1</b>,<b>username2</b>", bot, html=True
            )
            return
        # not lowercase usernames bug the api calls
    if len(args)>1:
        for username in args:
            if username!="":
                username_message_list.append(username.replace(" ","").replace(",",""))
    # len(args)==0 -> only one username or all in one line
    elif "," in args[0].lower():
        for splitted_username in args[0].lower().replace(" ","").rstrip().split(","):
            if splitted_username!="":
             username_message_list.append(splitted_username)
    else:
        username_message_list.append(args[0].lower())

    username_message_list=list(dict.fromkeys(username_message_list)) #remove duplicate usernames
    


    usernames_in_database = []
    # obtain present usernames
    results=Utils.retrieve_query_results(f"SELECT * FROM CHATURBATE WHERE CHAT_ID='{chatid}'")
    for row in results:
        usernames_in_database.append(row[0])


    # 0 is unlimited usernames
    if len(usernames_in_database)+len(username_message_list) > user_limit and (Utils.admin_check(chatid) == False != user_limit != 0):
        send_message(chatid, "You are trying to add more usernames than your limit permits, which is " + str(user_limit), bot)
        logging.info(f'{chatid} tried to add more usernames than his limit')
        return

    headers = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.110 Safari/537.36', }
     
    for username in username_message_list:
        try:
            target = f"https://en.chaturbate.com/api/chatvideocontext/{username}"
            response = requests.get(target, headers=headers)

            #check if the response can be actually be parsed
            if b"It's probably just a broken link, or perhaps a cancelled broadcaster." in response.content: #check if models still exists
                send_message(chatid, f"{username} was not added because it doesn't exist", bot)
                logging.info(f'{chatid} tried to add {username}, which does not exist')
                return
            else:
                response_json = json.loads(response.content)

            # check for not existing models and errors
            if (("status" in response_json) and ("401" in str(response_json['status'])) and ("This room requires a password" not in str(response_json['detail']))):
    
                if "Room is deleted" in str(response_json['detail']):
                    send_message(chatid, f"{username} has not been added because room has been deleted", bot)
                    logging.info(f"{chatid} could not add {username} because room has been deleted")

                if "This room has been banned" in str(response_json['detail']):
                    send_message(chatid, f"{username} has not been added because is banned", bot)
                    logging.info(f"{chatid} could not add {username} because is banned")

            else:
                if username not in usernames_in_database:
                        Utils.exec_query(f"INSERT INTO CHATURBATE VALUES ('{username}', '{chatid}', 'F')")
                        if 'detail' in response_json:
                            if "This room requires a password" in str(response_json['detail']):
                                send_message(chatid, f"{username} uses a password for his/her room, it has been added but tracking could be unstable", bot)
                                logging.info(f'{chatid} added {username}')
                        else:
                            send_message(chatid, f"{username} has been added", bot)
                            logging.info(f'{chatid} added {username}')
                else:
                    send_message(chatid, f"{username} has already been added", bot)

        except Exception as e:
            Utils.handle_exception(e)
            send_message(chatid, f"{username} was not added because an error happened", bot)
            logging.info(f'{chatid} could not add {username} because an error happened')


def remove(bot, update, args) -> None:
    chatid = update.message.chat.id
    username_message_list = []
    usernames_in_database=[]

    if len(args) < 1:
            send_message(
                chatid,
                "You need to specify an username to follow, use the command like /remove <b>test</b>\n You can also remove multiple users at the same time by separating them using a comma, like /remove <b>username1</b>,<b>username2</b>", bot, html=True
            )
            return
    if len(args)>1:
        for username in args:
            if username!="":
                username_message_list.append(username.replace(" ","").replace(",",""))
    # len(args)==0 -> only one username or all in one line
    elif "," in args[0].lower():
        for splitted_username in args[0].lower().replace(" ","").rstrip().split(","):
            if splitted_username!="":
             username_message_list.append(splitted_username)
    else:
        username_message_list.append(args[0].lower())

    results=Utils.retrieve_query_results(f"SELECT * FROM CHATURBATE WHERE CHAT_ID='{chatid}'")
    for row in results:
        usernames_in_database.append(row[0])

    if "all" in username_message_list:
        Utils.exec_query(
           f"DELETE FROM CHATURBATE WHERE CHAT_ID='{chatid}'")
        send_message(chatid, "All usernames have been removed", bot)
        logging.info(f"{chatid} removed all usernames")
    else:
        for username in username_message_list:
            if username in usernames_in_database:
                Utils.exec_query(f"DELETE FROM CHATURBATE WHERE USERNAME='{username}' AND CHAT_ID='{chatid}'")
                send_message(chatid, f"{username} has been removed", bot)
                logging.info(f"{chatid} removed {username}")
            else:
                send_message(chatid, f"You aren't following {username}", bot)


#Todo: test for null results list and improve the code
def list_command(bot, update) -> None:
    chatid = update.message.chat.id
    username_list = []
    online_list = []
    username_dict = {}
    followed_users = ""

    results=Utils.retrieve_query_results(f"SELECT * FROM CHATURBATE WHERE CHAT_ID='{chatid}'")
    if results!=[]: # an exception didn't happen
        for row in results:
            username_list.append(row[0])
            online_list.append(row[2])
        for x in range(0, len(username_list)):
            # dictionary with usernames and online_status
            username_dict.update({username_list[x]: online_list[x]})

        for username in sorted(username_dict):
            followed_users += username + ": "
            if username_dict[username] == "T":
                followed_users += "<b>online</b>\n"
            else:
                followed_users += "offline\n"

    if followed_users == "":
        send_message(chatid, "You aren't following any user", bot)
    else:
        send_message(
            chatid, f"You are currently following these {len(username_list)} users:\n" +
            followed_users, bot, html=True)

def stream_image(bot, update, args) -> None:
    chatid = update.message.chat.id

    if len(args) < 1:
        send_message(chatid, "You didn't specify a model to get the stream image of\nUse the command like this: /stream_image <b>username</b>", bot, html=True)
        return

    username=args[0].lower()
    if is_model_viewable(username):
        model_image=Image.open(BytesIO(requests.get(f'https://roomimg.stream.highwebmedia.com/ri/{username}.jpg').content))
        bio = BytesIO()
        bio.name = 'image.jpeg'
        model_image.save(bio, 'JPEG')
        bio.seek(0)
    
        bot.send_chat_action(chatid, action="upload_photo")
        bot.send_photo(chatid, bio)
        logging.info(f'{chatid} viewed {username} stream image')
    else:
        send_message(chatid, f"The model {username} is offline, private or does not exist", bot)
        logging.error(f'{chatid} failed to view {username} stream image')

def enable_link_preview(bot, update, args) -> None:
    chatid = update.message.chat.id

    if len(args) < 1:
        send_message(chatid,
                     "You didn't specify the setting\nUse the command like this:\n/enable_link_preview <b>true</b> to enable\n/enable_link_preview <b>false</b> to disable",
                     bot, html=True)
        return
    try:
        setting=Utils.str2bool(args[0].lower())
    except Exception as e:
        Utils.handle_exception(e)
        send_message(chatid,"An error happened with what you typed",bot)
    Preferences.update_link_preview_preference(chatid,setting)
    logging.info(f'{chatid} has set update_link_preview_preference to {setting}')
    send_message(chatid, f"The link preview preference has been set to {setting}", bot)


#endregion

#region admin functions

def authorize_admin(bot, update, args) -> None:

    chatid = update.message.chat_id
    if len(args) != 1:
        send_message(
            chatid,
            "You need to specify the admin password, use the command like /authorize_admin <b>password</b>", bot, html=True
        )
        return
    elif admin_pw == "":
        send_message(
            chatid,
            "The admin is disabled, check your telegram bot configuration", bot
        )
        return
    if Utils.admin_check(chatid):
        send_message(chatid, "You already are an admin", bot)
    elif args[0] == admin_pw:
        Utils.exec_query(f"""INSERT INTO ADMIN VALUES ({chatid})""")
        send_message(chatid, "Admin enabled", bot)
        send_message(chatid, "Remember to disable the --admin-password if you want to avoid people trying to bruteforce this command", bot)
        logging.info(f"{chatid} got admin authorization")
    else:
        send_message(chatid, "The password is wrong", bot)


def send_message_to_everyone(bot, update, args) -> None:
    chatid = update.message.chat.id

    if Utils.admin_check(chatid) == False:
        send_message(chatid, "You're not authorized to do this", bot)
        return

    chatid_list = []
    message = ""
    start_time=datetime.datetime.now()


    logging.info(f"{chatid} started sending a message to everyone")

    results=Utils.retrieve_query_results("SELECT DISTINCT CHAT_ID FROM CHATURBATE")
    for row in results:
        chatid_list.append(row[0])

    for word in args:
        message += word+" "
    message = message[:-1]

    for x in chatid_list:
        send_message(x, message, bot)
    logging.info(f"{chatid} finished sending a message to everyone, took {(datetime.datetime.now()-start_time).total_seconds()} seconds")


#endregion

#region threads

def check_online_status() -> None:
    global updater
    bot = updater.bot
    def update_status() -> None :
        username_list = []
        online_dict = {}
        chatid_dict = {}

        # create a dictionary with usernames and online using distinct
        results=Utils.retrieve_query_results("SELECT DISTINCT USERNAME,ONLINE FROM CHATURBATE")
        for row in results:
            online_dict[row[0]] = row[1]
            # username row0
            # online row1
        
        #create username_list
        for username in online_dict.keys():
            username_list.append(username)
        



        # obtain chatid
        for username in username_list:
                chatid_list = []
                results=Utils.retrieve_query_results(f"SELECT CHAT_ID FROM CHATURBATE WHERE USERNAME='{username}'")
                for row in results:
                    chatid_list.append(row[0])
                chatid_dict[username]=chatid_list            


        # Threaded function for queue processing.
        def crawl(q, response_dict):
            while not q.empty():
                work = q.get()                      #fetch new work from the Queue

                for attempt in range(5): #try to connect 5 times as there are a lot of network disruptions
                    try:
                        username=work[1]
                        target = f"https://en.chaturbate.com/api/chatvideocontext/{username.lower()}"
                        headers = {
                            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.110 Safari/537.36', }
                        response = requests.get(target, headers=headers)

                        if b"It's probably just a broken link, or perhaps a cancelled broadcaster." in response.content: #check if models still exists
                            logging.info(username.lower()+" is not a model anymore, removing from db")
                            Utils.exec_query(f"DELETE FROM CHATURBATE WHERE USERNAME='{username}'")
                            response_dict[username] = "error" #avoid processing the failed model
                        else:
                            response_json = json.loads(response.content)
                            response_dict[username] = response_json
    

    
                    except (json.JSONDecodeError,ConnectionError) as e:
                        Utils.handle_exception(e)
                        logging.info(username.lower()+" has failed to connect on attempt "+str(attempt))
                        time.sleep(1) #sleep and retry              
                    except Exception as e:
                        Utils.handle_exception(e)
                        response_dict[username] = "error"
                    
                    else:
                        break

                #signal to the queue that task has been processed
                q.task_done()
            return True

        q = Queue(maxsize=0)
        #Populating Queue with tasks
        response_dict = {}

        #load up the queue with the username_list to fetch and the index for each job (as a tuple):
        for i in range(len(username_list)):
            #need the index and the url in each queue item.
            q.put((i,username_list[i]))    
        
        #Starting worker threads on queue processing
        for i in range(http_threads):
            
            worker = threading.Thread(target=crawl, args=(q,response_dict), daemon=True)
            worker.start()
            time.sleep(wait_time)  #avoid server spamming by time-limiting the start of requests
        
        #now we wait until the queue has been processed
        q.join()
        
        
            
        

        for username in username_list:
            response=response_dict[username]
            
            try:

                if ("status" not in response
                        and response != "error"):
                    if (response["room_status"] == "offline"):

                        if online_dict[username] == "T" :
                            Utils.exec_query(
                                f"UPDATE CHATURBATE SET ONLINE='F' WHERE USERNAME='{username}'")

                            for y in chatid_dict[username]:
                                send_message(y, f"{username} is now offline", bot)


                    elif online_dict[username] == "F":

                            Utils.exec_query(
                                f"UPDATE CHATURBATE SET ONLINE='T' WHERE USERNAME='{username}'")

                            for y in chatid_dict[username]:    
                                send_message(y, f"{username} is now online! You can watch the live here: http://chaturbate.com/{username}", bot)

                            


                elif response != "error" and "401" in str(response['status']):
                        if "This room requires a password" in str(response['detail']) and (online_dict[username] == "F"): #assuming the user knows the password
                            Utils.exec_query(f"UPDATE CHATURBATE SET ONLINE='T' WHERE USERNAME='{username}'")
                            for y in chatid_dict[username]:
                                send_message(y, f"{username} is now online! You can watch the live here: http://chaturbate.com/{username}", bot)

                        if "Room is deleted" in str(response['detail']):
                            Utils.exec_query(f"DELETE FROM CHATURBATE WHERE USERNAME='{username}'")
                            for y in chatid_dict[username]:
                                send_message(y, f"{username} has been removed because room has been deleted", bot)
                            logging.info(f"{username} has been removed from database because room has been deleted")

                        if "This room has been banned" in str(response['detail']):
                            Utils.exec_query(f"DELETE FROM CHATURBATE WHERE USERNAME='{username}'")
                            for y in chatid_dict[username]:
                                send_message(y, f"{username} has been removed because room has been deleted", bot)
                            logging.info(f"{username} has been removed from database because has been banned")

                        if "This room is not available to your region or gender." in str(response['detail']):
                            Utils.exec_query(f"DELETE FROM CHATURBATE WHERE USERNAME='{username}'")
                            for y in chatid_dict[username]:
                                send_message(y, f"{username} has been removed because of geoblocking, I'm going to try to fix this soon", bot)
                            logging.info(f"{username} has been removed from database because of geoblocking")          
            except Exception as e:
                Utils.handle_exception(e)
    while(1):
        try:
            update_status()
        except Exception as e:
            Utils.handle_exception(e)

#endregion


start_handler = CommandHandler(('start', 'help'), start)
dispatcher.add_handler(start_handler)

add_handler = CommandHandler('add', add, pass_args=True)
dispatcher.add_handler(add_handler)

remove_handler = CommandHandler('remove', remove, pass_args=True)
dispatcher.add_handler(remove_handler)

list_handler = CommandHandler('list', list_command)
dispatcher.add_handler(list_handler)

stream_image_handler = CommandHandler('stream_image', stream_image, pass_args=True)
dispatcher.add_handler(stream_image_handler)

enable_link_preview_handler = CommandHandler('enable_link_preview', enable_link_preview, pass_args=True)
dispatcher.add_handler(enable_link_preview_handler)


authorize_admin_handler = CommandHandler(
    'authorize_admin', authorize_admin, pass_args=True)
dispatcher.add_handler(authorize_admin_handler)

send_message_to_everyone_handler = CommandHandler(
    'send_message_to_everyone', send_message_to_everyone, pass_args=True)
dispatcher.add_handler(send_message_to_everyone_handler)


logging.info('Checking database existence...')

# default table creation
Utils.exec_query("""CREATE TABLE IF NOT EXISTS CHATURBATE (
        USERNAME  CHAR(60) NOT NULL,
        CHAT_ID  CHAR(100),
        ONLINE CHAR(1))""")

# admin table creation
Utils.exec_query("""CREATE TABLE IF NOT EXISTS ADMIN (
        CHAT_ID  CHAR(100))""")

Utils.exec_query('''CREATE TABLE IF NOT EXISTS "PREFERENCES" (
	"CHAT_ID"	CHAR(100) UNIQUE,
	"LINK_PREVIEW"	INTEGER DEFAULT 1,
	PRIMARY KEY("CHAT_ID")
)''')

logging.info('Starting models checking thread...')
threading.Thread(target=check_online_status,daemon=True).start()

logging.info('Starting telegram polling thread...')
updater.start_polling()
updater.idle()
