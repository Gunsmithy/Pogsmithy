import sys
import asyncio
import socket
import websockets
import time
from datetime import datetime
import logging
import requests
import threading
import os
import argparse

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

banned_words_original = {'thanos', 'marvel', 'avengers', 'ironman', 'antman', 'hulk', 'endgame', 'thor', 'blackwidow',
                         'captainamerica', 'spiderman', 'spider-man', 'infinity', 'gauntlet'}
banned_words_permutated = set().union(banned_words_original)
leet_speak_map = {
    'a': '@',
    'o': '0',
    'i': '1',
    't': '7',
    's': '5',
    'e': '3',
    'b': '8'
}


def create_permutations(passed_word):
    index = 0
    while index < len(passed_word):
        original_word_start = passed_word[:index]
        original_word_end = passed_word[index:]
        for letter in original_word_end:
            if letter in leet_speak_map.keys():
                new_word = original_word_start + original_word_end.replace(letter, leet_speak_map[letter])
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

    def __init__(self, account_name):
        threading.Thread.__init__(self)
        self.account_name = account_name

    def run(self):
        get_rank_with_uuid(self.account_name)


def get_rank_from_number(number):
    rank_name_array = ['', '', '', '', '', 'Copper 3', 'Copper 2', 'Copper 1', 'Bronze 3', 'Bronze 2', 'Bronze 1',
                       'Silver 3', 'Silver 2', 'Silver 1', 'Gold 3', 'Gold 2', 'Gold 1',
                       'Plat 3', 'Plat 2', 'Plat 1', 'Diamond']

    return rank_name_array[number]


def get_rank_with_uuid(account_name):
    account_uuid = account_uuids[account_name]
    response = requests.get('https://r6tab.com/api/player.php?p_id=' + account_uuid + '&action=update')
    response_json = response.json()

    rank_names[account_name] = get_rank_from_number(int(response_json['p_NA_rank']))
    rank_mmrs[account_name] = str(response_json['p_NA_currentmmr'])

    rank_strings[account_name] = rank_names[account_name] + ' (' + rank_mmrs[account_name].replace('Current ', '') + ')'


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
        print("Error parsing this message: " + websocket_message)


async def connect_client(token, user):
    print('Attempting to connect...')
    websocket_client = await websockets.connect(twitch_wss_uri, ssl=True)
    await websocket_client.send("PASS {}".format(token))
    await websocket_client.send("NICK {}".format(user))
    name = await websocket_client.recv()
    print(f"< {name}")
    return websocket_client


async def join_channel(websocket_client, channel_name):
    await websocket_client.send("JOIN #{}".format(channel_name))
    stuff = await websocket_client.recv()
    print(f"< {stuff}")


async def request_capabilities(websocket_client):
    await websocket_client.send("CAP REQ :twitch.tv/membership")
    stuff = await websocket_client.recv()
    print(f"< {stuff}")
    await websocket_client.send("CAP REQ :twitch.tv/tags")
    stuff = await websocket_client.recv()
    print(f"< {stuff}")
    await websocket_client.send("CAP REQ :twitch.tv/commands")
    stuff = await websocket_client.recv()
    print(f"< {stuff}")


async def send_message(websocket_client, channel_name, message):
    await websocket_client.send("PRIVMSG #{} :{}".format(channel_name, message))
    # This kills the huge pogs
    # response = await websocket_client.recv()
    # print(f"< {response}")


async def handle_command(websocket_client, channel, user, command, args):
    if command == 'pogproxy':
        proxy_command = ' '.join(args)
        print(user + ' sent a pogproxy command: ' + proxy_command)
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
            monty_thread = RankUpdateThread('MontagneMontoya')
            monty_thread.start()
            monty_thread.join()
            rank_message = '/me MontagneMontoya: ' + rank_strings['MontagneMontoya']
        elif channel == 'sasslyn':
            burt_thread = RankUpdateThread('Burt-Macklin')
            dinger_thread = RankUpdateThread('Dinger-Bringer')
            jerry_thread = RankUpdateThread('Jerry-Gergich')
            doubleeh_thread = RankUpdateThread('DoubleEh7')
            burt_thread.start()
            dinger_thread.start()
            jerry_thread.start()
            doubleeh_thread.start()
            burt_thread.join()
            dinger_thread.join()
            jerry_thread.join()
            doubleeh_thread.join()
            rank_message = '/me Burt\'s Rank: ' + rank_strings['Burt-Macklin'] + \
                           ' // Dinger\'s Rank: ' + rank_strings['Dinger-Bringer'] + \
                           ' // Jerry\'s Rank: ' + rank_strings['Jerry-Gergich'] + \
                           ' // DE7\'s Rank: ' + rank_strings['DoubleEh7'] + \
                           ' sasslyFlex'
        else:
            rank_message = '/me I don\'t know you...'
        await send_message(websocket_client, channel, rank_message)
    elif command == "delhype":
        await send_message(websocket_client, channel,
                           'sasslySip sasslyHype sasslySip sasslyHype sasslySip sasslyHype sasslySip')
    else:
        print("Unrecognized command: " + command)


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

    # CHECK FOR POTENTIAL ENDGAME SPOILERS
    # if spoiler_check(e.arguments[0]):
    #     send_message(websocket_client, channel, "/timeout " + user + " 1")
    #     send_message(websocket_client, channel, "@" + user + " #DontSpoilTheEndgame marvHowdy")

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


async def handle_messages(websocket_client, channel_name):
    while True:
        try:
            received = await asyncio.wait_for(websocket_client.recv(), timeout=60.0)

            if received == "PING :tmi.twitch.tv\r\n":
                print('PING received, responding with PONG.')
                await websocket_client.send("PONG :tmi.twitch.tv\r\n")  # TODO - Maybe wrap this call in a wait_for too?

            tags, user, command, message = parse_irc_message(received)

            if command == "PRIVMSG":
                await handle_message(websocket_client, channel_name, user, message)
            elif command == "USERNOTICE":
                if tags.get('msg-id') == 'raid':
                    if int(tags['msg-param-viewerCount']) > 1:
                        shoutout_message = "HEY! Make sure you shoot {} a good ole follow over at http://twitch.tv/{}" \
                            .format(tags['msg-param-displayName'], tags['msg-param-login'])
                        await send_message(websocket_client, channel_name, shoutout_message)
            else:
                print("Not a PRIVMSG or USERNOTICE, outputting below:")
                print(f"< {received}")
        except asyncio.TimeoutError:
            # FIXME - Is this supposed to timeout every time?
            # FIXME - Only times out when no messages have been received in the timeout period set above, seems fine
            # print('Timeout: ' + datetime.datetime.now(pytz.timezone('America/Toronto')).isoformat(timespec='seconds'))
            pass


def read_secret_file(file_path):
    with open(file_path, 'r') as auth_file:
        return auth_file.readline()


def config():
    bot_user = os.getenv('POGSMITHY_TWITCH_USER')
    bot_channel = os.getenv('POGSMITHY_TWITCH_CHANNEL')
    bot_token = os.getenv('POGSMITHY_TWITCH_TOKEN')
    bot_token_file = os.getenv('POGSMITHY_TWITCH_TOKEN_FILE')

    parser = argparse.ArgumentParser(description='Run Pogsmithy for Twitch.')
    parser.add_argument('--user', dest='user', help='The Twitch User used to run the bot.')
    parser.add_argument('--channel', dest='channel', help='The Twitch channel in which to run the bot.')
    parser.add_argument('--token', dest='token', help='The Twitch OAuth token used to run the bot.')
    parser.add_argument('--token-file', dest='token_file', help='The path to the file containing the Twitch OAuth token'
                                                                ' used to run the bot.')
    args = parser.parse_args()

    if args.user is not None:
        print('Using --user command-line argument...')
        user = args.user
    elif bot_user is not None:
        print('Using POGSMITHY_TWITCH_USER environment variable...')
        user = bot_user
    else:
        print('Bot user could not be derived from environment or arguments. Aborting...')
        sys.exit(1)

    if args.channel is not None:
        print('Using --channel command-line argument...')
        channel = args.channel
    elif bot_channel is not None:
        print('Using POGSMITHY_TWITCH_CHANNEL environment variable...')
        channel = bot_channel
    else:
        print('Bot channel could not be derived from environment or arguments. Aborting...')
        sys.exit(1)

    if args.token is not None:
        print('Using --token command-line argument...')
        token = args.token
    elif args.token_file is not None:
        print('Using --token-file command-line argument...')
        token = read_secret_file(args.token_file)
    elif bot_token is not None:
        print('Using POGSMITHY_TWITCH_TOKEN environment variable...')
        token = bot_token
    elif bot_token_file is not None:
        print('Using POGSMITHY_TWITCH_TOKEN_FILE environment variable...')
        token = read_secret_file(bot_token_file)
    else:
        print('Bot token could not be derived from environment or arguments. Aborting...')
        sys.exit(1)

    return user, channel, token


def main(username, channel, token):
    while True:
        time.sleep(backoff_time)
        try:
            client = asyncio.get_event_loop().run_until_complete(connect_client(token, username))
            reset_backoff()
            asyncio.get_event_loop().run_until_complete(join_channel(client, channel))
            asyncio.get_event_loop().run_until_complete(request_capabilities(client))
            asyncio.get_event_loop().run_until_complete(handle_messages(client, channel))
        except websockets.exceptions.ConnectionClosedError:
            print('Oof, ConnectionClosedError')
        except socket.gaierror:
            print('Oof, gaierror')
            increase_backoff()


if __name__ == "__main__":
    config_user, config_channel, config_token = config()
    main(config_user, config_channel, config_token)