import sys
import irc.bot
from datetime import datetime
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient
import logging
import requests
from html.parser import HTMLParser
import threading

last_huge_pog = datetime.utcnow()
last_huge_squad = datetime.utcnow()
huge_pog_cooldown_seconds = 30
huge_squad_cooldown_seconds = 30

client_prefix = 'Pogsmithy-'
host = 'a2m4xpt70plp30.iot.us-east-1.amazonaws.com'
rootCAPath = 'root-CA.crt'
certificatePath = 'fd6ab5b8e9-certificate.pem.crt'
privateKeyPath = 'fd6ab5b8e9-private.pem.key'

# Port defaults
port = 8883

# Configure logging
logger = logging.getLogger("AWSIoTPythonSDK.core")
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
    'Blep.': 'da78484d-4a14-4fb8-948f-9e6f0fbc4b6e',
    'Blerp.-': 'e8d881dd-b113-4d47-aaa3-b1534cbd3cc5',
    'Bleep-.': 'b4ca4549-6b6b-46c5-acd6-16077b42693a'
}

# rank_message_dict = {
#     'gunsmithy': 'MontagneMontoya: {GET_RANK:MontagneMontoya}'
# }

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


class MyHTMLParser(HTMLParser):
    probably_current_rank = False
    probably_rank_name = False
    probably_rank_mmr = False
    probably_rank_na = False
    account_name = None

    def __init__(self, account):
        super(MyHTMLParser, self).__init__()
        self.account_name = account

    def error(self, message):
        pass

    def handle_starttag(self, tag, attrs):
        if tag == 'td':  # and len(attrs) > 0 and attrs[0] == ('class', 'currentrank'):
            print(attrs)
            self.probably_current_rank = True
        elif self.probably_current_rank and tag == 'font':
            self.probably_rank_name = True
        elif self.probably_rank_name and tag == 'div' and len(attrs) > 0 and attrs[0] == ('class', 'rankinfo'):
            self.probably_rank_name = False
            self.probably_rank_mmr = True
        elif self.probably_rank_mmr and tag == 's':
            self.probably_rank_na = True

    def handle_endtag(self, tag):
        pass

    def handle_data(self, data):
        if self.probably_current_rank and self.probably_rank_name:
            global rank_names
            rank_names[self.account_name] = data
        elif self.probably_rank_na and self.probably_rank_mmr:
            global rank_mmrs
            rank_mmrs[self.account_name] = data
            raise StopIteration
        pass


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
    # response = requests.get('https://r6tab.com/mainpage.php?page=/' + account_uuid)
    response = requests.get('https://r6tab.com/api/player.php?p_id=' + account_uuid + '&action=update')
    response_json = response.json()

    # parser = MyHTMLParser(account_name)
    # try:
    #     parser.feed(response.text)
    # except StopIteration:
    #     pass
    rank_names[account_name] = get_rank_from_number(int(response_json['p_NA_rank']))
    rank_mmrs[account_name] = str(response_json['p_NA_currentmmr'])

    rank_strings[account_name] = rank_names[account_name] + ' (' + rank_mmrs[account_name].replace('Current ', '') + ')'


class TwitchBot(irc.bot.SingleServerIRCBot):
    def __init__(self, username, client_id, token, channel):
        self.client_id = client_id
        if token.startswith('oauth:'):
            self.token = token
        else:
            self.token = 'oauth:' + token
        self.channel = '#' + channel

        # Create IRC bot connection
        server = 'irc.chat.twitch.tv'
        port = 6667
        print('Connecting to ' + server + ' on port ' + str(port) + '...')
        irc.bot.SingleServerIRCBot.__init__(self, [(server, port, self.token)], username, username)

    def on_welcome(self, c, e):
        print('Joining ' + self.channel)

        # You must request specific capabilities before you can use them
        c.cap('REQ', ':twitch.tv/membership')
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        c.join(self.channel)

    def on_privmsg(self, c, e):
        print("C: " + str(c))
        print("E: " + str(e))

    def on_privnotice(self, c, e):
        print("C: " + str(c))
        print("E: " + str(e))

    def on_featurelist(self, c, e):
        print("C: " + str(c))
        print("E: " + str(e))

    def on_pubmsg(self, c, e):
        global last_huge_pog
        global last_huge_squad

        msg_lower = e.arguments[0].lower()
        display_name = ''
        for tag in e.tags:
            if tag['key'] == 'display-name':
                display_name = tag['value']

        # CHECK FOR POTENTIAL ENDGAME SPOILERS
        # if spoiler_check(e.arguments[0]):
        #     c.privmsg(self.channel, "/timeout " + display_name + " 1")
        #     c.privmsg(self.channel, "@" + display_name + " #DontSpoilTheEndgame marvHowdy")

        # If a chat message starts with an exclamation point, try to run it as a command
        if e.arguments[0][:1] == '!':
            cmd = e.arguments[0].split(' ')[0][1:]
            print('Received command: ' + cmd)
            if cmd == "pogproxy":
                if display_name == 'Gunsmithy':
                    c.privmsg(self.channel, e.arguments[0][10:])
            else:
                self.do_command(e, cmd)
        # elif "tolby" in msg_lower or "tolbby" in msg_lower:
        #     c = self.connection
        #     c.privmsg(self.channel, "@" + display_name + " Did you mean Toby?")
        #     print(e)
        # elif "jizz" in msg_lower:
        #     c.privmsg(self.channel, "@" + display_name + " It's not jizz!!!")
        elif "huge pogs" in msg_lower:
            current_datetime = datetime.utcnow()
            if (current_datetime - last_huge_pog).seconds > huge_pog_cooldown_seconds or \
                    (current_datetime - last_huge_pog).days > 0:
                c.privmsg(self.channel, "psi1 psi2 psi3")
                c.privmsg(self.channel, "psi4 psi5 psi6")
                c.privmsg(self.channel, "psi7 psi8 psi9")
                last_huge_pog = current_datetime
        elif "huge squad" in msg_lower:
            channel_name = self.channel[1:]
            if channel_name == 'sasslyn':
                current_datetime = datetime.utcnow()
                if (current_datetime - last_huge_squad).seconds > huge_squad_cooldown_seconds or \
                        (current_datetime - last_huge_squad).days > 0:
                    c.privmsg(self.channel, "sasslySquad1 sasslySquad2 sasslySquad3")
                    c.privmsg(self.channel, "sasslySquad4 sasslySquad5 sasslySquad6")
                    c.privmsg(self.channel, "sasslySquad7 sasslySquad8 sasslySquad9")
                    last_huge_squad = current_datetime
        elif "lit" in msg_lower:
            channel_name = self.channel[1:]
            if channel_name == 'jrod0901':
                c.privmsg(self.channel, "blobSabers blobSabers blobSabers blobSabers blobSabers ")
        elif "pogchamp" in msg_lower or "poggers" in msg_lower:
            pog_champ_count = msg_lower.count("pogchamp")
            poggers_count = msg_lower.count("poggers")
            pog_count = pog_champ_count + poggers_count

            if pog_count == 1:
                c.privmsg(self.channel, "Pogs!")
            elif pog_count == 2:
                c.privmsg(self.channel, "POGGGGGERS!")
            elif pog_count == 3:
                c.privmsg(self.channel, "BIG POGS!")
            elif pog_count == 4:
                c.privmsg(self.channel, "HUUUUUGE POGS!")
            elif pog_count == 5:
                c.privmsg(self.channel, "MASSSSSIVE POGS!")
            elif pog_count > 5:
                current_datetime = datetime.utcnow()
                if (current_datetime - last_huge_pog).seconds > huge_pog_cooldown_seconds or \
                        (current_datetime - last_huge_pog).days > 0:
                    c.privmsg(self.channel, "psi1 psi2 psi3")
                    c.privmsg(self.channel, "psi4 psi5 psi6")
                    c.privmsg(self.channel, "psi7 psi8 psi9")
                    last_huge_pog = current_datetime
        elif "pogs" in msg_lower:
            pogs_count = msg_lower.count("pogs")
            poggers_string = ''
            for x in range(pogs_count):
                poggers_string += 'POGGERS '
            c.privmsg(self.channel, poggers_string)
        elif "iggyowSmile" in e.arguments[0]:
            smile_count = e.arguments[0].count("iggyowSmile")
            smile_string = ''
            for x in range(smile_count):
                smile_string += ':) '
            c.privmsg(self.channel, smile_string)
        elif "smile" in msg_lower or ":)" in msg_lower:
            smile_text_count = msg_lower.count("smile")
            smile_face_count = msg_lower.count(":)")
            smile_count = smile_text_count + smile_face_count
            smile_string = ''
            for x in range(smile_count):
                smile_string += 'iggyowSmile '
            c.privmsg(self.channel, smile_string)
        return

    def do_command(self, e, cmd):
        c = self.connection
        cmd = cmd.lower()

        if cmd == "paxy":
            c.privmsg(self.channel, "https://i.imgur.com/7mqx3DV.png")
        # elif cmd == "dylan":
        #     c.privmsg(self.channel,
        #               "Dylan is Gunsmithy. He is from Canada and he is not my brother or my boyfriend. normiesOUT")
        elif cmd == "iggy":
            c.privmsg(self.channel, "Unlucky my dood FeelsBadMan")
        elif cmd == "angery" or cmd == "grompy":
            c.privmsg(self.channel, ">:(")
        elif cmd == "dong":
            c.privmsg(self.channel, "Huge Dongers! ヽ༼ຈل͜ຈ༽ﾉ")
        elif cmd == "permitdylan":
            c.privmsg(self.channel, "!permit Gunsmithy")
        elif cmd == "bobs":
            c.privmsg(self.channel, '( CoolStoryBob )( CoolStoryBob )')
        elif cmd == "fortnite":
            c.privmsg(self.channel, 'https://streamable.com/wag7s')
        elif cmd == "vanish":
            display_name = ''
            for tag in e.tags:
                if tag['key'] == 'display-name':
                    display_name = tag['value']
            c.privmsg(self.channel, "/timeout " + display_name + " 1")
        elif cmd == "rank":
            # if rank_message_dict.get(self.channel):
            #     rank_message = rank_message_dict.get(self.channel)
            #     if 'GET_RAN'
            channel_name = self.channel[1:]
            if channel_name == 'gunsmithy':
                monty_thread = RankUpdateThread('MontagneMontoya')
                monty_thread.start()
                monty_thread.join()
                rank_message = '/me MontagneMontoya: ' + rank_strings['MontagneMontoya']
            elif channel_name == 'sasslyn':
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
            elif channel_name == 'jessicah':
                blep_thread = RankUpdateThread('Blep.')
                blerp_thread = RankUpdateThread('Blerp.-')
                bleep_thread = RankUpdateThread('Bleep-.')
                blep_thread.start()
                blerp_thread.start()
                bleep_thread.start()
                blep_thread.join()
                blerp_thread.join()
                bleep_thread.join()
                rank_message = '/me Blep\'s Rank: ' + rank_strings['Blep.'] + \
                               ' // Blerp\'s Rank: ' + rank_strings['Blerp.-'] + \
                               ' // Bleep\'s Rank: ' + rank_strings['Bleep-.'] + \
                               ' blerpHey'
            else:
                rank_message = '/me I don\'t know you...'
            c.privmsg(self.channel, rank_message)
        # The command was not recognized
        elif cmd == "delhype":
            c.privmsg(self.channel, 'sasslySip sasslyHype sasslySip sasslyHype sasslySip sasslyHype sasslySip')
        else:
            print("Unrecognized command: " + cmd)

    def send_message(self, message):
        c = self.connection
        c.privmsg(self.channel, message)


def main():
    if len(sys.argv) != 5:
        print("Usage: python chatbot.py <username> <client id> <token> <channel>")
        sys.exit(1)

    username = sys.argv[1]
    client_id = sys.argv[2]
    token = sys.argv[3]
    channel = sys.argv[4]

    # Init AWSIoTMQTTClient
    my_aws_iot_mqtt_client = AWSIoTMQTTClient(client_prefix + channel)
    my_aws_iot_mqtt_client.configureEndpoint(host, port)
    my_aws_iot_mqtt_client.configureCredentials(rootCAPath, privateKeyPath, certificatePath)

    # AWSIoTMQTTClient connection configuration
    my_aws_iot_mqtt_client.configureAutoReconnectBackoffTime(1, 32, 20)
    my_aws_iot_mqtt_client.configureOfflinePublishQueueing(-1)  # Infinite offline Publish queueing
    my_aws_iot_mqtt_client.configureDrainingFrequency(2)  # Draining: 2 Hz
    my_aws_iot_mqtt_client.configureConnectDisconnectTimeout(10)  # 10 sec
    my_aws_iot_mqtt_client.configureMQTTOperationTimeout(5)  # 5 sec

    my_aws_iot_mqtt_client.connect()

    bot = TwitchBot(username, client_id, token, channel)
    bot.start()


if __name__ == "__main__":
    main()
