import sys
import asyncio
import queue
import socket
import websockets
import time
from datetime import datetime
import logging
import signal
import requests
import threading
import os
import shutil
import argparse
import pickle
import os.path
import pytz
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

last_huge_pog = datetime.utcnow()
last_huge_squad = datetime.utcnow()
huge_pog_cooldown_seconds = 30
huge_squad_cooldown_seconds = 30

twitch_wss_uri = "wss://irc-ws.chat.twitch.tv:443"
max_backoff = 64
backoff_time = 1

# Port defaults
port = 8883

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
streamHandler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
streamHandler.setFormatter(formatter)
logger.addHandler(streamHandler)
log_rotation = 0

twitch_chat_logger = None
config_user = None
config_channel = None
config_token = None
config_tabwire_token = None
config_drive_folder_id = None
config_drive_pickle = None
log_timer = None
log_frequency = 86400  # 1 day
shoutout_queue = queue.Queue()

rank_names = {}
rank_mmrs = {}
rank_strings = {}
account_uuids = {
    'MontagneMontoya': '3cb45a4a-9208-48ac-8690-27dcbf1b6604',
    'Burt-Macklin': '8da0d780-e164-408b-ab03-26bc329346bb',
    'Dinger-Bringer': 'd8d20ab9-5747-43ed-a0fb-d0a2e0c0d75f',
    'Jerry-Gergich': 'ce351727-71cc-4a05-858a-a9dcd208d25c',
    'DoubleEh7': 'a0ae5914-21ae-4850-9482-fb10e294492e',
}

banned_words_original = {'joel', 'ellie', 'tlou', 'naughtydog', 'lastofus'}
banned_words_permutated = set().union(banned_words_original)
leet_speak_map = {
    'a': ['@'],
    'o': ['0'],
    'i': ['1', 'l', '!'],
    'l': ['1', 'i', '!'],
    't': ['7'],
    's': ['5'],
    'e': ['3'],
    'b': ['8']
}


def create_permutations(passed_word):
    index = 0
    while index < len(passed_word):
        original_word_start = passed_word[:index]
        original_word_end = passed_word[index:]
        for letter in original_word_end:
            if letter in leet_speak_map.keys():
                for new_letter in leet_speak_map[letter]:
                    new_word = original_word_start + original_word_end.replace(letter, new_letter)
                    if new_word not in banned_words_permutated:
                        banned_words_permutated.add(new_word)
                        create_permutations(new_word)
        index += 1


for original_word in banned_words_original:
    create_permutations(original_word)


def spoiler_check(message_passed):
    formatted = message_passed.replace(' ', '')
    formatted = formatted.lower()
    length = len(formatted)
    substring_list = [formatted[i:j + 1] for i in range(length) for j in range(i, length)]
    for substring in substring_list:
        if substring in banned_words_permutated:
            return True
    return False


class RankUpdateThread(threading.Thread):

    def __init__(self, tabwire_token, account_name):
        threading.Thread.__init__(self)
        self.tabwire_token = tabwire_token
        self.account_name = account_name

    def run(self):
        get_siege_rank_with_uuid(self.tabwire_token, self.account_name)


def get_short_siege_rank_from_number(number):
    rank_name_array = ['', '', '', '', '', 'Copper 3', 'Copper 2', 'Copper 1', 'Bronze 3', 'Bronze 2', 'Bronze 1',
                       'Silver 3', 'Silver 2', 'Silver 1', 'Gold 3', 'Gold 2', 'Gold 1',
                       'Plat 3', 'Plat 2', 'Plat 1', 'Diamond']

    return rank_name_array[number]


def update_siege_rank_with_uuid(tabwire_token, account_uuid):
    requests.get(f'https://r6.apitab.com/update/{account_uuid}?cid={tabwire_token}')


def get_siege_rank_with_uuid(tabwire_token, account_name):
    account_uuid = account_uuids[account_name]
    unix_stamp = int(datetime.utcnow().timestamp())

    update_siege_rank_with_uuid(tabwire_token, account_uuid)

    response = requests.get(f'https://r6.apitab.com/player/{account_uuid}?u={unix_stamp}&cid={tabwire_token}')
    response_json = response.json()

    rank_names[account_name] = get_short_siege_rank_from_number(response_json['ranked']['rank'])
    rank_mmrs[account_name] = str(response_json['ranked']['mmr'])

    if rank_names[account_name] == '' or rank_mmrs[account_name] == '0':
        rank_strings[account_name] = "Not placed"
    else:
        rank_strings[account_name] = f"{rank_names[account_name]} ({rank_mmrs[account_name]})"


def reset_backoff():
    global backoff_time
    backoff_time = 1


def increase_backoff():
    global backoff_time
    if backoff_time < max_backoff:
        backoff_time = backoff_time * 2


def parse_irc_message(websocket_message):
    try:
        tags = None
        if websocket_message.startswith('@'):
            tags = {}
            tags_string = websocket_message[1:websocket_message.find(' ')]
            tags_list = tags_string.split(';')
            for tag_string in tags_list:
                tag_split = tag_string.split('=')
                tags[tag_split[0]] = tag_split[1]
            tagless_message = websocket_message[websocket_message.find(' ')+1:]
        else:
            tagless_message = websocket_message

        user = None
        exclaim_delimiter_index = tagless_message.find('!')
        if exclaim_delimiter_index != -1 and exclaim_delimiter_index < tagless_message.find('tmi.twitch.tv'):
            user = tagless_message[1:exclaim_delimiter_index]

        command_index = tagless_message.find(' ')
        command = tagless_message[command_index+1:tagless_message.find(' ', command_index+1)]
        colon_index = tagless_message.find(':', 1)
        message = tagless_message[colon_index + 1:].rstrip()

        return tags, user, command, message
    except:
        logger.error("Error parsing this message: " + websocket_message)


async def connect_client(token, user):
    logger.info('Attempting to connect...')
    websocket_client = await websockets.connect(twitch_wss_uri, ssl=True)
    await websocket_client.send("PASS {}".format(token))
    await websocket_client.send("NICK {}".format(user))
    name = await websocket_client.recv()
    logger.debug(f"< {name}")
    return websocket_client


async def join_channel(websocket_client, channel_name):
    await websocket_client.send("JOIN #{}".format(channel_name))
    stuff = await websocket_client.recv()
    logger.debug(f"< {stuff}")


async def request_capabilities(websocket_client):
    await websocket_client.send("CAP REQ :twitch.tv/membership")
    stuff = await websocket_client.recv()
    logger.debug(f"< {stuff}")
    await websocket_client.send("CAP REQ :twitch.tv/tags")
    stuff = await websocket_client.recv()
    logger.debug(f"< {stuff}")
    await websocket_client.send("CAP REQ :twitch.tv/commands")
    stuff = await websocket_client.recv()
    logger.debug(f"< {stuff}")


async def send_message(websocket_client, channel_name, message):
    await websocket_client.send("PRIVMSG #{} :{}".format(channel_name, message))
    # This kills the huge pogs
    # response = await websocket_client.recv()
    # logger.debug(f"< {response}")


async def handle_command(websocket_client, channel, user, command, args):
    if command == 'pogproxy':
        proxy_command = ' '.join(args)
        logger.info(user + ' sent a pogproxy command: ' + proxy_command)
        if user == 'gunsmithy':
            await send_message(websocket_client, channel, proxy_command)
    elif command == "paxy":
        await send_message(websocket_client, channel, "https://i.imgur.com/7mqx3DV.png")
    # elif command == "dylan":
    #     c.privmsg(self.channel,
    #               "Dylan is Gunsmithy. He is from Canada and he is not my brother or my boyfriend. normiesOUT")
    elif command == "iggy":
        await send_message(websocket_client, channel, "Unlucky my dood FeelsBadMan")
    elif command == "angery" or command == "grompy":
        await send_message(websocket_client, channel, ">:(")
    elif command == "dong":
        await send_message(websocket_client, channel, "Huge Dongers! ヽ༼ຈل͜ຈ༽ﾉ")
    elif command == "permitdylan":
        await send_message(websocket_client, channel, "!permit Gunsmithy")
    elif command == "bobs":
        await send_message(websocket_client, channel, '( CoolStoryBob )( CoolStoryBob )')
    elif command == "fortnite":
        await send_message(websocket_client, channel, 'https://streamable.com/wag7s')
    elif command == "vanish":
        await send_message(websocket_client, channel, "/timeout " + user + " 1")
    elif command == "rank":
        if channel == 'gunsmithy':
            monty_thread = RankUpdateThread(config_tabwire_token, 'MontagneMontoya')
            monty_thread.start()
            monty_thread.join()
            rank_message = '/me MontagneMontoya: ' + rank_strings['MontagneMontoya']
        elif channel == 'sasslyn':
            burt_thread = RankUpdateThread(config_tabwire_token, 'Burt-Macklin')
            dinger_thread = RankUpdateThread(config_tabwire_token, 'Dinger-Bringer')
            jerry_thread = RankUpdateThread(config_tabwire_token, 'Jerry-Gergich')
            doubleeh_thread = RankUpdateThread(config_tabwire_token, 'DoubleEh7')
            burt_thread.start()
            dinger_thread.start()
            jerry_thread.start()
            doubleeh_thread.start()
            burt_thread.join()
            dinger_thread.join()
            jerry_thread.join()
            doubleeh_thread.join()
            try:
                rank_message = '/me Burt\'s Rank: ' + rank_strings['Burt-Macklin'] + \
                               ' // Dinger\'s Rank: ' + rank_strings['Dinger-Bringer'] + \
                               ' // Jerry\'s Rank: ' + rank_strings['Jerry-Gergich'] + \
                               ' // DE7\'s Rank: ' + rank_strings['DoubleEh7'] + \
                               ' sasslyFlex'
            except KeyError:
                rank_message = "Woops, can't get your rank right now. Let Gunsmithy know or try again."
        else:
            rank_message = '/me I don\'t know you...'
        await send_message(websocket_client, channel, rank_message)
    elif command == "delhype":
        await send_message(websocket_client, channel,
                           'sasslySip sasslyHype sasslySip sasslyHype sasslySip sasslyHype sasslySip')
    else:
        logger.debug("Unrecognized command: " + command)


async def huge_pogs(websocket_client, channel):
    global last_huge_pog
    current_datetime = datetime.utcnow()
    if (current_datetime - last_huge_pog).seconds > huge_pog_cooldown_seconds or \
            (current_datetime - last_huge_pog).days > 0:
        await send_message(websocket_client, channel, "psi1 psi2 psi3")
        await send_message(websocket_client, channel, "psi4 psi5 psi6")
        await send_message(websocket_client, channel, "psi7 psi8 psi9")
        last_huge_pog = current_datetime


async def handle_message(websocket_client, channel, user, message):
    global last_huge_squad

    msg_lower = message.lower()

    # CHECK FOR POTENTIAL LAST OF US 2 SPOILERS
    if spoiler_check(message):
        await send_message(websocket_client, channel, "/timeout " + user + " 1")
        await send_message(websocket_client, channel, "@" + user + " No Last of Us 2 spoilers! marvHowdy")

    # If a chat message starts with an exclamation point, try to run it as a command
    if message.startswith('!'):
        if ' ' in message:
            args = message[message.find(' ')+1:].split(' ')
            await handle_command(websocket_client, channel, user, message[1:message.find(' ')], args)
        else:
            await handle_command(websocket_client, channel, user, message[1:], None)

    # Otherwise, look for other fun stuff in the message
    elif "huge pogs" in msg_lower:
        await huge_pogs(websocket_client, channel)
    elif "huge squad" in msg_lower:
        if channel == 'sasslyn':
            current_datetime = datetime.utcnow()
            if (current_datetime - last_huge_squad).seconds > huge_squad_cooldown_seconds or \
                    (current_datetime - last_huge_squad).days > 0:
                await send_message(websocket_client, channel, "sasslySquad1 sasslySquad2 sasslySquad3")
                await send_message(websocket_client, channel, "sasslySquad4 sasslySquad5 sasslySquad6")
                await send_message(websocket_client, channel, "sasslySquad7 sasslySquad8 sasslySquad9")
                last_huge_squad = current_datetime
    elif " lit " in msg_lower or msg_lower.startswith('lit ') or msg_lower.endswith(' lit') or msg_lower == 'lit':
        if channel == 'jrod0901':
            await send_message(websocket_client, channel, "blobSabers blobSabers blobSabers blobSabers blobSabers")
    elif "pogchamp" in msg_lower or "poggers" in msg_lower:
        pog_champ_count = msg_lower.count("pogchamp")
        poggers_count = msg_lower.count("poggers")
        pog_count = pog_champ_count + poggers_count

        if pog_count == 1:
            await send_message(websocket_client, channel, "Pogs!")
        elif pog_count == 2:
            await send_message(websocket_client, channel, "POGGGGGERS!")
        elif pog_count == 3:
            await send_message(websocket_client, channel, "BIG POGS!")
        elif pog_count == 4:
            await send_message(websocket_client, channel, "HUUUUUGE POGS!")
        elif pog_count == 5:
            await send_message(websocket_client, channel, "MASSSSSIVE POGS!")
        elif pog_count > 5:
            await huge_pogs(websocket_client, channel)
    elif "pogs" in msg_lower:
        pogs_count = msg_lower.count("pogs")
        poggers_string = ''
        for x in range(pogs_count):
            poggers_string += 'POGGERS '
        await send_message(websocket_client, channel, poggers_string)
    elif "iggyowSmile" in message:
        smile_count = message.count("iggyowSmile")
        smile_string = ''
        for x in range(smile_count):
            smile_string += ':) '
        await send_message(websocket_client, channel, smile_string)
    elif "smile" in msg_lower or ":)" in msg_lower:
        smile_text_count = msg_lower.count("smile")
        smile_face_count = msg_lower.count(":)")
        smile_count = smile_text_count + smile_face_count
        smile_string = ''
        for x in range(smile_count):
            smile_string += 'iggyowSmile '
        await send_message(websocket_client, channel, smile_string)


async def shoutout_run_loop():
    while True:
        queue_item = shoutout_queue.get()
        if queue_item is None:
            break
        time.sleep(5)
        await shout_out(queue_item['WebsocketClient'], queue_item['ChannelName'], queue_item['DisplayName'],
                        queue_item['Login'])


class ShoutoutThread(threading.Thread):

    def __init__(self):
        asyncio.get_event_loop()
        threading.Thread.__init__(self)

    def run(self):
        asyncio.new_event_loop().run_until_complete(shoutout_run_loop())


async def shout_out(websocket_client, channel_name, display_name, login):
    shout_out_message = f"HEY! Make sure you shoot {display_name} a good ole follow over at https://twitch.tv/{login}"
    await send_message(websocket_client, channel_name, shout_out_message)


async def handle_messages(websocket_client, channel_name, chat_logger):
    while True:
        try:
            received = await asyncio.wait_for(websocket_client.recv(), timeout=60.0)

            if received == "PING :tmi.twitch.tv\r\n":
                logger.debug('PING received, responding with PONG.')
                await websocket_client.send("PONG :tmi.twitch.tv\r\n")  # TODO - Maybe wrap this call in a wait_for too?
                continue

            tags, user, command, message = parse_irc_message(received)

            if command == "PRIVMSG":
                chat_logger.info(f'{user} - {message}')
                await handle_message(websocket_client, channel_name, user, message)
            elif command == "USERNOTICE":
                if tags.get('msg-id') == 'raid':
                    if int(tags['msg-param-viewerCount']) > 1:
                        shoutout_queue.put({
                            "WebsocketClient": websocket_client,
                            "ChannelName": channel_name,
                            "DisplayName": tags['msg-param-displayName'],
                            "Login": tags['msg-param-login']
                        })
            else:
                logger.debug("Not a PRIVMSG or USERNOTICE, outputting below:")
                logger.debug(f"< {received}")
        except asyncio.TimeoutError:
            # current_time_string = datetime.now(pytz.timezone('America/Toronto')).isoformat(timespec='seconds')
            # logger.debug(f'Timeout: {current_time_string}')
            pass
        except KeyboardInterrupt:
            shutdown()


def read_secret_file(file_path):
    with open(file_path, 'r') as auth_file:
        return auth_file.readline()


def config():
    bot_user = os.getenv('POGSMITHY_TWITCH_USER')
    bot_channel = os.getenv('POGSMITHY_TWITCH_CHANNEL')
    bot_token = os.getenv('POGSMITHY_TWITCH_TOKEN')
    bot_token_file = os.getenv('POGSMITHY_TWITCH_TOKEN_FILE')
    bot_tabwire_token = os.getenv('POGSMITHY_TWITCH_TABWIRE_TOKEN')
    bot_tabwire_token_file = os.getenv('POGSMITHY_TWITCH_TABWIRE_TOKEN_FILE')
    bot_drive_folder = os.getenv('POGSMITHY_TWITCH_GDRIVE_FOLDER')
    bot_drive_pickle_file = os.getenv('POGSMITHY_TWITCH_GDRIVE_PICKLE')

    parser = argparse.ArgumentParser(description='Run Pogsmithy for Twitch.')
    parser.add_argument('--user', dest='user', help='The Twitch User used to run the bot.')
    parser.add_argument('--channel', dest='channel', help='The Twitch channel in which to run the bot.')
    parser.add_argument('--token', dest='token', help='The Twitch OAuth token used to run the bot.')
    parser.add_argument('--token-file', dest='token_file', help='The path to the file containing the Twitch OAuth token'
                                                                ' used to run the bot.')
    parser.add_argument('--tabwire-token', dest='tabwire_token', help='The Tabwire API token used to call their APIs.')
    parser.add_argument('--tabwire-token-file', dest='tabwire_token_file', help='The path to the file containing the '
                                                                                'Tabwire API token.')
    parser.add_argument('--gdrive-folder', dest='gdrive_folder', help='The ID of the Google Drive folder for chat logs'
                                                                      'if desired.')
    parser.add_argument('--gdrive-pickle', dest='gdrive_pickle', help='The path to the Google Drive credentials pickle '
                                                                      'file if uploading chat logs.')
    args = parser.parse_args()

    if args.user is not None:
        logger.info('Using --user command-line argument...')
        user = args.user
    elif bot_user is not None:
        logger.info('Using POGSMITHY_TWITCH_USER environment variable...')
        user = bot_user
    else:
        logger.error('Bot user could not be derived from environment or arguments. Aborting...')
        sys.exit(1)

    if args.channel is not None:
        logger.info('Using --channel command-line argument...')
        channel = args.channel
    elif bot_channel is not None:
        logger.info('Using POGSMITHY_TWITCH_CHANNEL environment variable...')
        channel = bot_channel
    else:
        logger.error('Bot channel could not be derived from environment or arguments. Aborting...')
        sys.exit(1)

    if args.token is not None:
        logger.info('Using --token command-line argument...')
        token = args.token
    elif args.token_file is not None:
        logger.info('Using --token-file command-line argument...')
        token = read_secret_file(args.token_file)
    elif bot_token is not None:
        logger.info('Using POGSMITHY_TWITCH_TOKEN environment variable...')
        token = bot_token
    elif bot_token_file is not None:
        logger.info('Using POGSMITHY_TWITCH_TOKEN_FILE environment variable...')
        token = read_secret_file(bot_token_file)
    else:
        logger.error('Bot token could not be derived from environment or arguments. Aborting...')
        sys.exit(1)

    if args.tabwire_token is not None:
        logger.info('Using --tabwire-token command-line argument...')
        tabwire_token = args.tabwire_token
    elif args.tabwire_token_file is not None:
        logger.info('Using --tabwire-token-file command-line argument...')
        tabwire_token = read_secret_file(args.tabwire_token_file)
    elif bot_tabwire_token is not None:
        logger.info('Using POGSMITHY_TWITCH_TABWIRE_TOKEN environment variable...')
        tabwire_token = bot_tabwire_token
    elif bot_tabwire_token_file is not None:
        logger.info('Using POGSMITHY_TWITCH_TABWIRE_TOKEN_FILE environment variable...')
        tabwire_token = read_secret_file(bot_tabwire_token_file)
    else:
        logger.error('Tabwire API token could not be derived from environment or arguments. Aborting...')
        sys.exit(1)

    if args.gdrive_folder is not None:
        logger.info('Using --gdrive-folder command-line argument...')
        folder_id = args.gdrive_folder
    elif bot_drive_folder is not None:
        logger.info('Using POGSMITHY_TWITCH_GDRIVE_FOLDER environment variable...')
        folder_id = bot_drive_folder
    else:
        logger.info('No Google Drive folder ID provided. Chat logs will not be uploaded.')
        folder_id = None

    if args.gdrive_pickle is not None:
        logger.info('Using --gdrive-pickle command-line argument...')
        drive_pickle_file = args.gdrive_pickle
    elif bot_drive_pickle_file is not None:
        logger.info('Using POGSMITHY_TWITCH_GDRIVE_PICKLE environment variable...')
        drive_pickle_file = bot_drive_pickle_file
    else:
        logger.info('No Google Drive pickle file provided. Chat logs will not be uploaded.')
        drive_pickle_file = None

    return user, channel, token, tabwire_token, folder_id, drive_pickle_file


def create_chat_logger():
    # Logger for chat messages
    chat_logger = logging.getLogger("ChatLogger")
    chat_logger.setLevel(logging.INFO)
    chat_file_log_handler = logging.FileHandler('twitch-chat-0.log', mode='w')
    chat_file_log_handler.setLevel(logging.INFO)
    chat_formatter = logging.Formatter('%(asctime)s - %(message)s')
    chat_file_log_handler.setFormatter(chat_formatter)
    chat_logger.addHandler(chat_file_log_handler)

    return chat_logger


def upload_log_file(log_name, folder_id, drive_pickle):
    if not folder_id:
        logger.debug('Folder ID was not provided. Deleting chat logs...')
        os.remove(log_name)
        return
    if not drive_pickle:
        logger.debug('Drive pickle was not provided. Deleting chat logs...')
        os.remove(log_name)
        return
    if os.path.getsize(log_name) == 0:
        logger.debug(f'{log_name} is empty. Deleting chat logs...')
        os.remove(log_name)
        return
    else:
        logger.debug(f'{log_name} is not empty. Uploading...')

    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is created automatically when the
    # authorization flow completes for the first time.
    if os.path.exists(drive_pickle):
        with open(drive_pickle, 'rb') as token:
            creds = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', ['https://www.googleapis.com/auth/drive'])
                creds = flow.run_local_server(port=0)
            except (FileNotFoundError, IOError) as e:
                logger.error('Credentials expired and could not open credentials.json. Probably running in Docker.'
                             'Not uploading or deleting log file.')
                return
        # Save the credentials for the next run
        with open(drive_pickle, 'wb') as token:
            pickle.dump(creds, token)

    drive_service = build('drive', 'v3', credentials=creds)

    file_metadata = {
        'name': log_name,
        'parents': [folder_id],
    }
    media = MediaFileUpload(log_name, mimetype='text/plain')
    drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    del media
    os.remove(log_name)


def move_log_file(channel_name):
    global log_rotation
    current_time_string = datetime.now(pytz.timezone('America/Toronto')).isoformat(timespec='seconds').replace(':', '.')
    logger.debug(f'Moving log file {log_rotation} at {current_time_string}')
    new_log_name = f'Twitch-Chat-{channel_name}-{current_time_string}.txt'
    shutil.move(f'twitch-chat-{log_rotation}.log', new_log_name)

    return new_log_name


def rotate_log_file(channel_name, folder_id, chat_logger):
    global log_rotation, log_timer
    chat_file_log_handler = logging.FileHandler(f'twitch-chat-{log_rotation+1}.log', mode='w')
    chat_file_log_handler.setLevel(logging.INFO)
    chat_formatter = logging.Formatter('%(asctime)s - %(message)s')
    chat_file_log_handler.setFormatter(chat_formatter)
    chat_logger.addHandler(chat_file_log_handler)
    chat_logger.removeHandler(chat_logger.handlers[0])
    new_log_name = move_log_file(channel_name)
    upload_log_file(new_log_name, folder_id, config_drive_pickle)
    log_rotation += 1
    log_timer = threading.Timer(log_frequency, rotate_log_file, [config_channel, config_drive_folder_id, chat_logger])
    log_timer.start()


def shutdown():
    logger.info('Shutting down...')
    twitch_chat_logger.removeHandler(twitch_chat_logger.handlers[0])
    new_log_name = move_log_file(config_channel)
    upload_log_file(new_log_name, config_drive_folder_id, config_drive_pickle)
    log_timer.cancel()
    shoutout_queue.put(None)
    sys.exit(1)


def main(username, channel, token, chat_logger):
    while True:
        time.sleep(backoff_time)
        try:
            client = asyncio.get_event_loop().run_until_complete(connect_client(token, username))
            reset_backoff()
            asyncio.get_event_loop().run_until_complete(join_channel(client, channel))
            asyncio.get_event_loop().run_until_complete(request_capabilities(client))
            asyncio.get_event_loop().run_until_complete(handle_messages(client, channel, chat_logger))
        except websockets.exceptions.ConnectionClosedError:
            logger.error('Oof, ConnectionClosedError')
        except socket.gaierror:
            logger.error('Oof, gaierror')
            increase_backoff()
        except KeyboardInterrupt:
            shutdown()


def receive_signal(signal_number, frame):
    if signal_number == signal.SIGINT or signal_number == signal.SIGTERM:
        shutdown()
    return


if __name__ == "__main__":
    config_user, config_channel, config_token, config_tabwire_token, config_drive_folder_id, config_drive_pickle = \
        config()
    twitch_chat_logger = create_chat_logger()
    signal.signal(signal.SIGINT, receive_signal)
    signal.signal(signal.SIGTERM, receive_signal)
    log_timer = threading.Timer(log_frequency, rotate_log_file,
                                [config_channel, config_drive_folder_id, twitch_chat_logger])
    log_timer.start()
    shoutout_thread = ShoutoutThread()
    shoutout_thread.start()
    main(config_user, config_channel, config_token, twitch_chat_logger)
