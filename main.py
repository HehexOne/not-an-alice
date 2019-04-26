import os
import yandex_search
import apiai
import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import keys
import additional
import requests
from telegram.ext import Updater, MessageHandler, Filters, CallbackQueryHandler
from apixu.client import ApixuClient
import logging
from azure.cognitiveservices.search.imagesearch import ImageSearchAPI
from msrest.authentication import CognitiveServicesCredentials
from newsapi import NewsApiClient

logging.basicConfig(filename='main.log',
                    format='%(asctime)s %(levelname)s %(name)s %(message)s',
                    level=logging.DEBUG)

newsapi = NewsApiClient(api_key=keys.news_api)
app = apiai.ApiAI(keys.apiai)
yandex = yandex_search.Yandex(api_key=keys.yandex_key, api_user=keys.yandex_user)
client = ApixuClient(keys.apixu)
image_search = ImageSearchAPI(credentials=CognitiveServicesCredentials(keys.visual_search_key))

session_storage = {}
err = " Если у вас постоянно возникает ошибка с поиском, поиском по изображению или новостями," \
      " то рекомендую вам перезапустить меня командой /start ."


def get_toponym_delta(toponym):
    toponym_bounded_lower = tuple(
        toponym["boundedBy"]["Envelope"]["lowerCorner"].split(
            " "))
    toponym_bounded_upper = tuple(
        toponym["boundedBy"]["Envelope"]["upperCorner"].split(
            " "))
    return str(abs(float(toponym_bounded_lower[0]) -
                   float(toponym_bounded_upper[0]))), \
           str(abs(float(toponym_bounded_lower[1]) -
                   float(toponym_bounded_upper[1])))


def get_weather(city):
    current = client.current(q=city)
    city_get = current["location"]["name"]
    current = current['current']
    weather = requests.get("https://translate.yandex.net/api/v1.5/tr.json/translate", params={
        "key": keys.translator,
        "text": "The weather is " + current["condition"]["text"],
        "lang": "ru"
    }).json()['text'][0]
    return f"Погода в {city} на данный момент:\n\n" \
               f"{weather.replace('Погода ', '').capitalize()}\n" \
               f"Градусы цельсия: {current['temp_c']}\n" \
               f"Чувствуется как: {current['feelslike_c']}\n" \
               f"Влажность: {current['humidity']}%\n" \
               f"Давление: {current['pressure_mb']}\n" \
               f"Направление ветра: {current['wind_dir']}\n" \
               f"Скорость ветра: {current['wind_kph']}", city_get


def get_response(text, session_id):
    request = app.text_request()
    request.lang = "ru"
    request.session_id = session_id
    request.query = text
    response = json.loads(request.getresponse().read().decode('utf-8'))
    try:
        return response['result']['fulfillment']['speech']
    except Exception:
        return "Произошла ошибка, попробуйте позднее."


def recieved_message(bot, update):
    response = get_response(update.message.text,
                            str(update.message.from_user.id))
    if response.startswith("json"):
        response = json.loads(response.replace("json", ""))
        try:
            if response["command"] == "translate":
                translated_text = requests.get("https://translate.yandex.net/api/v1.5/tr.json/translate", params={
                    "key": keys.translator,
                    "text": response["text"],
                    "lang": additional.langs[response["lang"]]
                }).json()['text'][0]
                update.message.reply_text(f"\"{response['text']}\" на"
                                          f" {response['lang'].capitalize()}")
                update.message.reply_text(f"{translated_text}")
            elif response["command"] == "weather":
                update.message.reply_text("Минуточку, сверяю данные по базам...")
                caption, city = get_weather(response["city"])
                coords = requests.get("https://geocode-maps.yandex.ru/1.x/"
                                      f"?geocode={city}&format=json").json()[
                    "response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]
                point = coords["Point"]["pos"]
                delta = get_toponym_delta(coords)
                bot.send_photo(chat_id=update.message.chat_id, caption=caption,
                               photo=f"https://static-maps.yandex.ru/1.x/?ll={','.join(point.split())}"
                               f"&l=map&spn={','.join(delta)}")
            elif response["command"] == "search":
                session_storage[update.message.from_user.id]["results"] = yandex.search(query=response["value"]).items
                keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_results')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                update.message.reply_text(f"Результаты по запросу \"{response['value']}\"")
                result = session_storage[update.message.from_user.id]["results"].pop(0)
                bot.send_message(chat_id=update.message.from_user.id,
                                 text=f"{result['title']}\n\n{result['snippet']}\n{result['url']}",
                                 reply_markup=reply_markup)
            elif response["command"] == "image_search":
                session_storage[update.message.from_user.id]["image_results"] = image_search.images. \
                    search(query=response["value"]).value
                keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_image_results')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                result = session_storage[update.message.from_user.id]["image_results"].pop(0).content_url
                bot.send_photo(chat_id=update.message.chat_id, photo=result,
                               caption=f"Результаты по запросу \"{response['value']}\"", reply_markup=reply_markup)
            elif response["command"] == "news":
                if response["value"] == "NaN":
                    response["value"] = ""
                top_headlines = newsapi.get_top_headlines(q=response["value"],
                                                          language='ru',
                                                          country='ru')
                if not top_headlines:
                    update.message.reply_text("Таких новостей в нашей базе не нашлось! Попробуйте \"Новости бизнес\"")
                    return
                session_storage[update.message.from_user.id]["news_results"] = top_headlines["articles"]
                keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_news_results')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                result = session_storage[update.message.from_user.id]["news_results"].pop(0)
                if response['value'] == "":
                    response["value"] = "Последние новости"
                update.message.reply_text(f"Результаты по запросу \"{response['value']}\"")
                try:
                    bot.send_photo(chat_id=update.message.chat_id, photo=result["urlToImage"],
                                   caption=f"{result['title']}\n\n{result['description']}\nПодробнее: {result['url']}",
                                   reply_markup=reply_markup)
                except Exception:
                    bot.send_message(chat_id=update.message.chat_id,
                                     text=f"{result['title']}\n\n{result['description']}\nПодробнее: {result['url']}",
                                     reply_markup=reply_markup)
            else:
                update.message.reply_text("Извините, но данная комманда временно недоступна.")
        except Exception as e:
            logging.error(e)
            update.message.reply_text("Извините, не удалось выполнить ваш запрос." + err)
    else:
        if response == "detect" or response == "find":
            update.message.reply_text("Извините, мне не понятен ваш запрос. Если вы хотите получить ответ связаный"
                                      " с изображением, то прикрепите фотографию к вашему сообщению.")
        else:
            update.message.reply_text(response)


def recieved_command(bot, update):
    if update.message.text == "/start":
        session_storage[update.message.from_user.id] = {}
        update.message.reply_text("Привет! Я - твой персональный ассистент в Telegram! (Ну, почти) "
                                  "Ты можешь спросить у меня погоду,"
                                  " попросить перевести текст или показать мне картинку, а я скажу,"
                                  " что на ней изображено, и попытаюсь найти похожую, а так же могу поискать"
                                  " что-то для вас в интернете (включая картинки) или показать последние новости!\n"
                                  "/help - помощь по функционалу.")
    elif update.message.text == "/help":
        update.message.reply_text("Какая из функций вас интересует?\n"
                                  "/start - вывести приветствие\n"
                                  "/help - вывести помощь по командам\n"
                                  "/help_translate - помощь с переводчиком\n"
                                  "/help_weather - помощь с погодой\n"
                                  "/help_photo - помощь с изображениями\n"
                                  "/help_search - помощь с поиском\n"
                                  "/help_news - помощь с новостями")
    elif update.message.text == "/help_translate":
        update.message.reply_text("Переводчиком пользоваться очень просто! Попробуй написать \"Переведи <фраза> на "
                                  "<язык>\" и я скажу тебе, как перевести данную фразу на язык,"
                                  " на который ты пожелаешь!\n\nПример: \"Переведи Привет на Английский\"")
    elif update.message.text == "/help_weather":
        update.message.reply_text("Запросить текущую погоду можно по команде: \"Погода в <Название города>\"."
                                  "\n\nПример: \"Погода в Москве\"")
    elif update.message.text == "/help_photo":
        update.message.reply_text("В моих алгоритмах описаны два способа взаимодействия с фотографиями:\n"
                                  "1. Ты можешь спросить у меня что изображено на фотографии. Для этого "
                                  "тебе нужно отправить фотографию и сделать подпись \"Что тут изображено?\"\n"
                                  "2. Ты можешь попросить меня найти похожие фотографии. Для этого "
                                  "тебе нужно отправить фотографию с подписью \"Найди похожие\"")
    elif update.message.text == "/help_search":
        update.message.reply_text("Одна из моих функций - поиск сайтов. Можешь попросить меня найти что-то командой"
                                  " \"Поиск <Запрос>\"\n\nТак же ты можешь попросить меня найти картинки"
                                  " \"Покажи картинки по запросу <Запрос>\"")
    elif update.message.text == "/help_news":
        update.message.reply_text("Я могу показывать тебе свежие новости прямо в мессенджере!\n"
                                  "Чтобы получить список последних новостей, напиши \"Новости\"\n"
                                  "Чтобы получить список новостей по запросу, напиши \"Новости <Запрос>\"")
    else:
        update.message.reply_text("Такой команды не существует! Введите /help для экскурса по командам.")


def recieved_photo(bot, update):
    resp = get_response(update.message.caption, str(update.message.from_user.id))
    if resp == "detect":
        try:
            update.message.reply_text("Подождите, дайте приглядеться...")
            file = bot.getFile(update.message.photo[-1].file_id)
            headers = {
                'Content-Type': 'application/octet-stream',
                'Ocp-Apim-Subscription-Key': keys.cognitive_key,
            }
            file.download(f"{file.file_id}.jpeg")
            with open(f"{file.file_id}.jpeg", "rb") as pic:
                resp = requests.post("https://northeurope.api.cognitive.microsoft.com/vision/v2.0/describe",
                                     headers=headers, data=pic.read()).json()
            os.remove(f"{file.file_id}.jpeg")
            translated_text = requests.get("https://translate.yandex.net/api/v1.5/tr.json/translate", params={
                "key": keys.translator,
                "text": resp["description"]["captions"][0]["text"],
                "lang": "ru"
            }).json()['text'][0]
            update.message.reply_text(f"Я думаю, что на этом изображении {translated_text}.\n\nС вероятностью "
                                      f"{resp['description']['captions'][0]['confidence']}")
        except Exception as e:
            update.message.reply_text("К сожалению, мои сервера зрения заняты и я тут ничего не вижу...")
            logging.warning(e)
    elif resp == "find":
        try:
            update.message.reply_text("Подождите, дайте приглядеться...")
            file = bot.getFile(update.message.photo[-1].file_id)
            headers = {
                'Ocp-Apim-Subscription-Key': keys.visual_search_key,
            }
            file.download(f"{file.file_id}.jpeg")
            with open(f"{file.file_id}.jpeg", "rb") as pic:
                resp = requests.post("https://api.cognitive.microsoft.com/bing/v7.0/images/visualsearch",
                                     headers=headers, files={'image': ('myfile', pic)}).json()
            os.remove(f"{file.file_id}.jpeg")
            a = [i for i in resp["tags"][0]["actions"] if i['actionType'] == "VisualSearch"][0]["data"]["value"]
            session_storage[update.message.from_user.id]["images"] = [i["contentUrl"] for i in a]
            keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_images')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            bot.send_photo(chat_id=update.message.chat_id, caption="Найдены похожие изображения по вашему запросу.",
                           photo=session_storage[update.message.from_user.id]["images"].pop(0),
                           reply_markup=reply_markup)
        except Exception as e:
            update.message.reply_text("К сожалению, мои сервера зрения заняты и я тут ничего не вижу..." + err)
            logging.warning(e)
    else:
        update.message.reply_text("Извините, мне не понятен ваш запрос.")


def callback_query_handler(bot, update):
    if update.callback_query.data == "more_images":
        try:
            keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_images')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            bot.send_photo(chat_id=update.callback_query.from_user.id,
                           photo=session_storage[update.callback_query.from_user.id]["images"].pop(0),
                           reply_markup=reply_markup)
            bot.answer_callback_query(update.callback_query.id, text="")
        except Exception as e:
            if not session_storage[update.callback_query.from_user.id].get("images", None):
                bot.send_message(chat_id=update.callback_query.from_user.id,
                                 text="Не вижу больше похожих изображений..." + err)
            logging.warning(e)
    elif update.callback_query.data == "more_results":
        try:
            keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_results')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            result = session_storage[update.callback_query.from_user.id]["results"].pop(0)
            bot.send_message(chat_id=update.callback_query.from_user.id,
                             text=f"{result['title']}\n\n{result['snippet']}\n{result['url']}",
                             reply_markup=reply_markup)
            bot.answer_callback_query(update.callback_query.id, text="")
        except Exception as e:
            if not session_storage[update.callback_query.from_user.id].get("results", None):
                bot.send_message(chat_id=update.callback_query.from_user.id,
                                 text="Больше ничего нет..." + err)
            logging.warning(e)
    elif update.callback_query.data == "more_image_results":
        try:
            keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_image_results')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            result = session_storage[update.callback_query.from_user.id]["image_results"].pop(0).content_url
            bot.send_photo(chat_id=update.callback_query.from_user.id, photo=result, reply_markup=reply_markup)
            bot.answer_callback_query(update.callback_query.id, text="")
        except Exception as e:
            if not session_storage[update.callback_query.from_user.id].get("image_results", None):
                bot.send_message(chat_id=update.callback_query.from_user.id,
                                 text="Больше ничего нет..." + err)
            logging.warning(e)
    elif update.callback_query.data == "more_news_results":
        try:
            keyboard = [[InlineKeyboardButton("Ещё ⬇", callback_data='more_news_results')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            result = session_storage[update.callback_query.from_user.id]["news_results"].pop(0)
            try:
                bot.send_photo(chat_id=update.callback_query.from_user.id, photo=result["urlToImage"],
                               caption=f"{result['title']}\n\n{result['description']}\nПодробнее: {result['url']}",
                               reply_markup=reply_markup)
            except Exception:
                bot.send_message(chat_id=update.callback_query.from_user.id,
                                 text=f"{result['title']}\n\n{result['description']}\nПодробнее: {result['url']}",
                                 reply_markup=reply_markup)
            bot.answer_callback_query(update.callback_query.id, text="")
        except Exception as e:
            if not session_storage[update.callback_query.from_user.id].get("news_results", None):
                bot.send_message(chat_id=update.callback_query.from_user.id,
                                 text="Больше нет новых новостей по такому запросу..." + err)
            logging.warning(e)
    else:
        bot.send_message(chat_id=update.callback_query.from_user.id,
                         text="Извините, что-то пошло не так...")
    bot.answer_callback_query(update.callback_query.id, text="")


def main():
    updater = Updater(keys.telegram)
    dp = updater.dispatcher
    text_handler = MessageHandler(Filters.text, recieved_message)
    command_handler = MessageHandler(Filters.command, recieved_command)
    photo_handler = MessageHandler(Filters.photo, recieved_photo)
    callback_handler = CallbackQueryHandler(callback_query_handler)
    dp.add_handler(text_handler)
    dp.add_handler(command_handler)
    dp.add_handler(photo_handler)
    dp.add_handler(callback_handler)
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
