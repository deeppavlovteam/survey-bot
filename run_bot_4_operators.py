import csv
import random
from datetime import datetime
from itertools import chain
from typing import List, Tuple, Dict, Any
import re
import uuid

import os
from collections import namedtuple, defaultdict

import pickle
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler

import html

INPUT_FILE = 'downloads/test_predict_243k_balanced_2911_0.csv'
CACHE_FILE = INPUT_FILE + '_4operators.pickle'
OUTPUT_FILE = 'target/test_predict_243k_balanced_2911_0__4operators_context_{}.tsv'.format(datetime.now().strftime('%Y%m%dT%H%M%S'))
TOKEN = os.environ['SENSE_BOT_TOKEN']
OPERATOR_RANDOM = 'random'
OPERATOR_HUMAN = 'human'
OPERATOR_BOT_FIRST = 'botfirst'
OPERATOR_BOT_BEST = 'botbest'
OPERATOR_BOT_RETR = 'botretr'

# TODO: add OPERATOR_BOT_RETR
OPERATORS = [OPERATOR_HUMAN, OPERATOR_BOT_FIRST, OPERATOR_BOT_BEST, OPERATOR_RANDOM]

operators_map = {'0': OPERATOR_BOT_FIRST,
                 '1': OPERATOR_HUMAN,
                 '2': OPERATOR_BOT_RETR}

Row = namedtuple('Row', 'id context question answer operator discriminator')


def prepare_dataset(filename=INPUT_FILE) -> Dict[str, List[Row]]:
    contexts = defaultdict(list)
    with open(filename) as f:
        csvfile = csv.reader(f, delimiter=',')
        next(csvfile)
        index = 0
        while True:
            try:
                text, is_human, discriminator_score = next(csvfile)
                context, *_ = text.split(' <ANS_START> ')
                chunks = re.findall(r'(<[A-Z_]+> [^<>]*)', text)
                answer = chunks[-1]

                assert answer.startswith('<ANS_START> '), text
                answer = answer.replace('<ANS_START> ', '')

                if chunks[-2].startswith('<MAN_START> '):
                    continue
                if chunks[-2].startswith('<PAUSE> '):
                    continue

                assert chunks[-2].startswith('<COR_START> '), [chunks[-2], text]
                question = chunks[-2].replace('<COR_START> ', '')

                if ('????????????????????????' in answer.lower()) and ('c?????????? ?????????????????????? ??????????????????' in answer.lower()):
                    print(answer)
                    continue

                operator = operators_map[is_human]

                row = Row(index, context, question, answer, operator, float(discriminator_score))

                contexts[context].append(row)
                index += 1
            except StopIteration:
                break
            except IndexError:
                pass
    return contexts


def get_best_and_random_answer(dataset):
    human_answers = []
    for rows in dataset.values():
        for r in rows:
            if r.operator == OPERATOR_HUMAN:
                human_answers.append(r.answer)

    for context, rows in dataset.items():
        rows = list(rows)
        if len(rows) == 1:
            continue

        human_rows = [r for r in rows if r.operator == OPERATOR_HUMAN]
        if not human_rows:
            continue
        bot_rows = [r for r in rows if r.operator != OPERATOR_HUMAN]

        if not bot_rows:
            continue

        best_row = max(bot_rows, key=lambda x: x.discriminator)
        values = dict(zip(Row._fields, best_row))
        values['operator'] = OPERATOR_BOT_BEST
        best_row = Row(**values)

        first_row = bot_rows[0]

        values = dict(zip(Row._fields, random.choice(bot_rows)))
        values['operator'] = OPERATOR_RANDOM
        values['answer'] = random.choice(human_answers)
        random_row = Row(**values)

        #TODO: add botretr here
        yield human_rows[0], best_row, first_row, random_row


def shuffle(dataset):
    dataset = list(chain(*dataset))
    random.shuffle(dataset)
    return dataset


def prepare_message(message_store: Dict[str, Any], instance: Tuple[int, Row]):
    questions_asked, row = instance

    # message = "{row.question}\n<b>??????????:</b>\n{row.answer}".format(row=row)
    message = "{question}\n<b>??????????:</b>\n{answer}".format(question=html.escape(row.context), answer=row.answer)

    time_asked = datetime.now().isoformat()

    uid = uuid.uuid1().hex

    message_store[uid] = {'row': row, 'time_asked': time_asked}

    button_list = [
        [InlineKeyboardButton('????????????????????', callback_data='{};1'.format(uid)),
         InlineKeyboardButton('???? ????????????????????', callback_data='{};0'.format(uid))],
    ]
    reply_markup = InlineKeyboardMarkup(button_list)

    return questions_asked, message, reply_markup


def main():
    updater = Updater(token=TOKEN)
    dispatcher = updater.dispatcher

    dialogs = {}

    exists = os.path.isfile(OUTPUT_FILE)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    messages_store = {}

    if not os.path.isfile(CACHE_FILE):
        print('Creating cache file {} ...'.format(CACHE_FILE))
        dataset = shuffle(get_best_and_random_answer(prepare_dataset(INPUT_FILE)))
        with open(CACHE_FILE, 'wb') as f:
            pickle.dump(dataset, f)
        print('Created!')

    with open(CACHE_FILE, 'rb') as f:
        dataset = pickle.load(f)

    with open(OUTPUT_FILE, 'a', newline='', encoding='utf-8') as tsvfile:
        writer = csv.writer(tsvfile, delimiter='\t')

        if not exists:
            writer.writerow(['chat_id', 'user', 'is_meaningful', 'operator', 'question', 'answer', 'context',
                             'discriminator', 'time_asked', 'time_answered'])
            tsvfile.flush()

        def start(bot: Bot, update: Update):
            chat_id = update.message.chat_id
            dataset_rows = list(dataset)
            random.shuffle(dataset_rows)
            dialogs[chat_id] = {
                'batch_generator': iter(enumerate(dataset_rows))
            }

            startup_message = '''???????????? ????????! ???????????? ?????? ?????????? ???????????????????????? ?????????????????? ???? ???????? ?????????????????? ?????????????????? ?????????? ?? ????????????????. ???????????? ?????? ?????????????? ?????????? ?????????????????? ???? ???????????? ?????????????? ???? ?????????????? ??????????????????????????. ?????????????????????????? ?????????????????? ?????? ???????? ???????????????????????? ???????????????? ????????, ?????? ???????????????? ???????????????? ???????????? ?????????????? ?? ???????????????? ????????????.

???????????? 10 ????????????????????, ?????????????? ?????????? ???????????????? ???????????????????? ?????????????????? ??????????????.
'''

            bot.send_message(chat_id=chat_id, text=startup_message)

            i, message, reply_markup = prepare_message(messages_store, next(dialogs[chat_id]['batch_generator']))
            bot.send_message(chat_id=chat_id, text=message,
                             reply_markup=reply_markup, parse_mode='HTML')

        def reply(bot: Bot, update: Update):
            query = update.callback_query
            chat_id = query.message.chat_id
            user = (update.effective_user.first_name or '') + '@' + (update.effective_user.username or '')

            uid, result = query.data.split(';')
            if uid in messages_store:
                row = messages_store[uid]['row']
                time_asked = messages_store[uid]['time_asked']

                writer.writerow([chat_id, user, result, row.operator, row.question, row.answer,
                                 row.context, row.discriminator, time_asked, datetime.now().isoformat()])
                tsvfile.flush()
                # bot.send_message(chat_id=chat_id, text=row.operator)

            if chat_id not in dialogs:
                start(bot, query)
            else:
                i, message, reply_markup = prepare_message(messages_store, next(dialogs[chat_id]['batch_generator']))
                if i > 0 and i % 10 == 0:
                    bot.send_message(chat_id=chat_id, text='<i>???? ???????????????? ???? {} ????????????????</i>'.format(i),
                                     parse_mode='HTML')
                bot.send_message(chat_id=chat_id, text=message,
                                 reply_markup=reply_markup, parse_mode='HTML')

        dispatcher.add_handler(CommandHandler('start', start))
        dispatcher.add_handler(CallbackQueryHandler(reply))

        updater.start_polling()

        updater.idle()


if __name__ == '__main__':
    main()
