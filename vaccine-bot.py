## Includes for Utils 
import logging
from datetime import date
import re
import json

## Includes for API requests
import requests
from requests.exceptions import HTTPError

## Includes for Telegram API etc
from telegram import Update, ForceReply, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Updater, 
    CommandHandler, 
    MessageHandler, 
    Filters, 
    CallbackContext, 
    ConversationHandler,
)

# Setting up logging
logging.basicConfig(filename='app.log',format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Reading a json file containing the state and district details, available in the COWIN API listed under metadata API
json_file = open('state-dist-map.json') 
state_dist_map = json.load(json_file)

# Creating a vertical list for viewing
vert_view_state_list = []
for state in state_dist_map.keys():
    vert_view_state_list += [[ state ]]


# Enum for conversation states
CHOOSE_QUERY_METHOD ,CHOOSE_STATE ,CHOOSE_PIN ,CHOOSE_DISTRICT, FINISH, HELP = range(6)

# Enum for request type for the function create_http_request
BY_DIST , BY_PIN = range(2)




################################################################## Utility functions

"""

function_name   :  send_http_request
input           :  req_type        ->   enum identifying the type of request to be made
                   req_details     ->   integer containing district id or pincode depending on req_type
output          :  returns None if error invalid input, returns response from request otherwise
description     :  creates http request based on req_type and req_details provided and returns None or response obtained

"""

def send_http_request(req_type,req_details):
    today = date.today().strftime("%d-%m-%Y")   
    if req_type == BY_DIST:
        req_url = f'https://cdn-api.co-vin.in/api/v2/appointment/sessions/public/calendarByDistrict?district_id={req_details}&date={today}'
    elif req_type == BY_PIN:
        req_url = f'https://cdn-api.co-vin.in/api/v2/appointment/sessions/public/calendarByPin?pincode={req_details}&date={today}'
    else :
        return None
    try :
        response = requests.get(req_url)
        response.raise_for_status()
    except HTTPError as http_err:
        logging.error(f'HTTP error occurred :: {http_err}')
        return None
    except exception as err:
        logging.error(f'Error occurred :: {err}')
        return None
    else :
        return response




"""
function_name       :   print_calendar
input               :   resp_obj    ->  The Response object obtained from querying the calendar of  COWIN-api by district or pincode in JSON 
                        update      ->  Updater context of current message received to provide replies to
output              :   None
Description         :   This function takes a JSON response object and updater context and then notifies user of the Calendars of centers with atleast one open slot 
"""

def print_calendar(resp_obj,update):
    ## Find centers where at least one session with either dose1 or dose 2 is available
    available_centers = [ center for center in resp_obj['centers'] if ( [] != [session for session in center['sessions'] if (session['available_capacity_dose1']+session['available_capacity_dose2'] > 0)]) ]

    ## If not centers with atleast one session available notify no sessions available
    if ( [] == available_centers):
        update.message.reply_text("No available vaccines in the next 7 days")
        return

    ## Iterate through centers that have available sessions
    for center in available_centers:
        c_rsp_str = f'The vaccine calendar at {center["name"]}'
        update.message.reply_text(c_rsp_str,reply_markup=ReplyKeyboardRemove(),)

        ## Find sessions with atleast one slot either dose1 or dose2 and print session details
        available_sessions = [ session for session in center['sessions'] if (session["available_capacity_dose1"]+session["available_capacity_dose2"] > 0) ]
        for session in center['sessions']:
            s_rsp_str = f'''
Date            :: {session["date"]}
Free/Paid       :: {center["fee_type"]}     
Vaccine         :: {session["vaccine"]}
Min age         :: {session["min_age_limit"]}
Dose 1          :: {session["available_capacity_dose1"]}
Dose 2          :: {session["available_capacity_dose2"]}'''
            update.message.reply_text(s_rsp_str)
    update.message.reply_text("Trying booking with the Cowin app :: https://selfregistration.cowin.gov.in ")
    return
    



"""

function_name       :   util_validate_pincode
input               :   pincode - the text entered by user when prompted for PIN-code
output              :   True or False based on validity of pincode provided
description         :   This function is used to Validate the pincode provided by the user, first against a regex and then again INDIA post api

"""

def util_validate_pincode(pincode):
    ## API to check if URL exists at postal pincode
    req_url= 'https://api.postalpincode.in/pincode/' + str(pincode)
    ## Six digit pin code or not??
    if re.match(r'^[1-9][0-9]{5}$',str(pincode)):
        try:
            response = requests.get(req_url)
            response.raise_for_status()
        except HTTPError as http_err:
            logging.error(f'HTTPError in validate_pincode{http_err}')
        except Exception as error:
            logging.error(f'HTTPError in util_validate_pincode {http_err}')
        else :
    ## Taking the first object from the response     
            json_obj = response.json()[0]
            if (json_obj['Status'] == "Error"):
                logging.warning(f'Invalid PIN CODE {pincode}')
                return False
            else :
                return True
    else :
        return False
        
        

################################################################### Command handlers

"""
function_name       :   start
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(CHOOSE_QUERY_METHOD) -> taking bot to conversation state registered in ConversationHandler
description         :   This function handles the /start command and starts a conversation
                        Provides custom keyboard with two options to continue the conversation and returns STATE CHOOSE_QUERY_METHOD
"""
def start(update,cb_context):
    """Send a message when the command /start is issued."""
    query_options = [["District","PIN-code"]]
    user = update.effective_user
    logger.info(f'New Conversation started by :: {user.name}')
    ## Send a welcome message
    update.message.reply_markdown_v2(fr'Hey there {user.mention_markdown_v2()}\!')
    ##  Take input on what parameter to query by
    update.message.reply_text("choose how you'd like to find your vaccine slots",reply_markup=ReplyKeyboardMarkup(query_options,one_time_keyboard=True))
    return CHOOSE_QUERY_METHOD




############################################ Conversation handlers bydistrict case




"""
function_name       :   choose_state
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(CHOOSE_STATE) -> taking bot to conversation state registered in ConversationHandler
description         :   Continues conversation from when user chooses to query by district
                        Provides custom keyboard with states in alphabetical order to choose from 
                        return STATE CHOOSE_STATE
"""


def choose_state(update,cb_context):
    """Find the vaccination calendar for the next 7 days, query by district id"""

    ## Set query_option in user_data
    cb_context.user_data['query_option'] = BY_DIST
    ## List out the states vertically for the user to choose from and go to next state CHOOSE_STATE
    update.message.reply_text("Great, you've chosen to find the vaccines by district")
    update.message.reply_text("Find your state in the list below...",reply_markup=ReplyKeyboardMarkup(vert_view_state_list,one_time_keyboard=True))

    return CHOOSE_STATE



"""
function_name       :   choose_district
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(CHOOSE_DISTRICT) -> taking bot to conversation state registered in ConversationHandler
description         :   Continues conversation from when user chooses State to query 
                        Provides custom keyboard with districts in the chosen state in alphabetical order to choose from 
                        return STATE CHOOSE_DISTRICT
"""

def choose_district(update,cb_context):
    
    chosen_state = update.message.text
    
    ##  Check if the Input is valid
    if ( chosen_state in state_dist_map.keys() ):

        ## If valid store in to callback context for later use
        logging.info(f'The user :: {update.effective_user} has chosen the valid state {chosen_state}')
        cb_context.user_data['chosen_state'] = chosen_state
        update.message.reply_text(f'The state you have chosen is :: {chosen_state}')

        ## Create a vertical list view of the districts in that state
        vert_view_district_list = []
        for district in state_dist_map[chosen_state]['districts'].keys():
            vert_view_district_list += [[ district ]]

        ## Print the vertical list and go to the next state CHOOSE_DISTRICT
        update.message.reply_text("Now find your district in the list below",reply_markup=ReplyKeyboardMarkup(vert_view_district_list,one_time_keyboard=True))
        return CHOOSE_DISTRICT
    else :
        ## If an invalid state was chosen, show the list again, this only happens when user doesn't use the provided one time keyboard
        logging.warning(f'The user :: {update.effective_user} has chosen an invalid state {chosen_state}')
        update.message.reply_text("That option was not in our list, could you please choose from the drop down list below rather than typing :)")
        return CHOOSE_STATE


"""
function_name       :   find_calendar_bydistrict
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(CHOOSE_DISTRICT)           -> In case the user entered Invalid district redirect to start of this state
                        ENUM(ConversationHandler.END)   -> In case valid user Input and response creation was okay, Cleanup and end the conversation
description         :   Continues conversation from when user chooses District to query 
                        Validates the choices made till now
                        create HTTP request with details provided if valid response received notify user
"""


def find_calendar_bydistrict(update,cb_context):

    ## Check if input is valid and use the district id to create request

    if ('query_option' not in cb_context.user_data):
        ## command /bydistrict case
        if ([] != cb_context.args):
            district_details = cb_context.args[0]
            logging.info(f'command call to /bydistrict by {update.effective_user.name} using dist-id {cb_context.args[0]}')
        else:
            update.message.reply_text('You might have missed out the district id :( ')
            return cleanup(update,cb_context)
    else :
        ## ConversationHandler case
        ## Get the chosen_state name from the callback context and the chosen_district from the update text
        chosen_state = cb_context.user_data['chosen_state']
        chosen_district = update.message.text

        if ( (chosen_district in state_dist_map[chosen_state]['districts'].keys()) and ('query_option' in cb_context.user_data) ):
            ## If valid store in to callback context for later use
            logging.info(f'The user :: {update.effective_user} has chosen the valid district {chosen_district}')
            update.message.reply_text(f'The district you have chosen is :: {chosen_district}')

            ## Get the district details from state_dist_map using chosen_state and chosen_district
            district_details = state_dist_map[chosen_state]['districts'][chosen_district]
            cb_context.user_data['chosen_district'] = district_details
        else :
            ## If an invalid state was chosen, show the list again, this only happens when user doesn't use the provided one time keyboard
            logging.warning(f'The user :: {update.effective_user} has chosen an invalid district {chosen_district}')
            update.message.reply_text('''
That option was not in our list :(
1. Try typing the district
2. Choose the dropdown and try again
3. Press -> /cancel and restart conversation 
''')
            return CHOOSE_DISTRICT

    ## Create and send a http request to get a response with vaccine data
    response = send_http_request(BY_DIST,district_details)
    if response == None:
        ## If response not received log and notify error and exit
        if ('query_option' not in cb_context.user_data):
            logging.error('Error in either dist id or http error')
            update.message.reply_text(f'Entered district ID maybe wrong start the app with /start')
        logging.error("Error in creating response")
        update.message.reply_text('''
Something went wrong :( ....
try again later''')
        return cleanup(update,cb_context)
    else :
        ## If valid response received notify user
        logging.info("Received response")
        resp_obj = response.json()
        print_calendar(resp_obj,update)
        return cleanup(update,cb_context)


####################################### Conversation handlers PINCODE case   


'''

function_name       :   enter_pincode
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(CHOOSE_PIN)           -> In case the user entered Invalid district redirect to start of this state
description         :   Continues conversation after user chooses to query by pincode, gets the user_data
'''


def enter_pincode(update,cb_context):
    """Find the vaccination calendar for the next 7 days, query by district id"""
    cb_context.user_data['query_option'] = BY_PIN
    update.message.reply_text(fr'Enter the PINCODE',reply_markup=ForceReply(selective=True))

    return CHOOSE_PIN


'''


function_name       :   find_calendar_bypincode
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(ConversationHandler.END)           -> End the conversation as the last step
description         :   Continues conversation after user chooses PINCODE
                        Validates the PINCODE provided creates http request and sends if valid
                        if valid response object received notifies user


'''


def find_calendar_bypincode(update,cb_context):
    if ('query_option' not in cb_context.user_data):
        if ( [] != cb_context.args):
            logging.info(f'command call to /bypincode by user :: {update.effective_user.name} with pincode :: {cb_context.args[0]}')
            chosen_pincode = cb_context.args[0]
        else :
            update.message.reply_text('You might have missed out the PINCODE  :( ')
            update.message.reply_text('Type done and try again')
            return cleanup(update,cb_context)
            
    elif (BY_PIN == cb_context.user_data['query_option']):
        chosen_pincode = update.message.text
    if (util_validate_pincode(chosen_pincode)):
        logging.info(f'The user :: {update.effective_user} has entered a valid pin')
        update.message.reply_text(f"The Entered PIN :: {chosen_pincode} is valid")
        cb_context.user_data['chosen_pincode'] = chosen_pincode
        response = send_http_request(BY_PIN,chosen_pincode)
        if response == None:
            logging.error("Error in creating response")
            update.message.reply_text(f'Unable to get response try again later')
            return cleanup(update,cb_context)
        else:
            logging.info("Received valid response")
            resp_obj = response.json()
            print_calendar(resp_obj,update)
            return cleanup(update,cb_context)
            

    else:
        logging.warning(f'The user :: {update.effective_user} has entered an invalid pin')
        update.message.reply_text("The Entered PIN is incorrect, try again with valid PINCODE ..",reply_markup=ForceReply(selective=True))
        return CHOOSE_PIN


'''


function_name       :   cleanup
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(ConversationHandler.END)           -> End the conversation as the last step
description         :   Last step in any conversation
                        Cleanup the user context
                        Provide usage information
                        Ends conversation and takes state back to entry_point


'''


######################################### Miscellanous callback functions


def cleanup(update,cb_context):
    """Cleanup and provide command for how to repeat the search straight away"""
    ### Thank you message
    update.message.reply_text("Hope this bot helped you") 

    ### Message on how to replicate query without going through conversation
    if ('query_option' in cb_context.user_data):
        '''

        Made this part to make  repeat queries easier but it seems to make people more confused
        if (BY_DIST == cb_context.user_data['query_option']):
            update.message.reply_text(f'To repeat the same exact query ')
            update.message.reply_text(f'type /bydistrict {cb_context.user_data["chosen_district"]}')
            update.message.reply_text(f'Don\'t forget to give the district ID as well :)')
            update.message.reply_text(f'/bydistrict <dist-id>')
            update.message.reply_text(f'In your case district id is :: {cb_context.user_data["chosen_district"]}')
        elif (BY_PIN == cb_context.user_data['query_option']):
            update.message.reply_text(f'To repeat the same exact query ')
            update.message.reply_text(f'type /bypincode {cb_context.user_data["chosen_pincode"]}')
            update.message.reply_text(f'Don\'t forget to give the PINCODE as well :)')
            update.message.reply_text(f'/bypincode <pincode>')
            update.message.reply_text(f'In your case PINCODE is :: {cb_context.user_data["chosen_pincode"]}')
        '''
    update.message.reply_text('''
press -> /start to start conversation 
press -> /cancel between query to force stop
press -> /about for more info about the bot''')
    if ([] != cb_context.args):
        cb_context.args = []

    ### Cleanup this conversation and end it
    if ('chosen_district' in  cb_context.user_data):
        del cb_context.user_data['chosen_district']
    if ('chosen_state' in  cb_context.user_data):
        del cb_context.user_data['chosen_state']
    if ('chosen_pincode' in  cb_context.user_data):
        del cb_context.user_data['chosen_pincode']
    if ('query_option' in  cb_context.user_data):
        del cb_context.user_data['query_option']
    cb_context.user_data.clear()
    logging.info(f'Finished cleanup for {update.effective_user.name}')

    return ConversationHandler.END
    
    
"""

function_name       :   about
input               :   update          ->  the updater context for the current message 
                        cb_context      ->  callback context to get access to args and outside context
output              :   ENUM(ConversationHandler.END)           -> End the conversation as the last step
description         :   This function provides the user with info about the bot

"""

def about(update,cb_context):
    logging.info(f'User :: {update.effective_user.name} used /about')
    update.message.reply_text('''
This bot was built using the python-telegram-bot  
Using data from from COWIN/API-Setu

Here\'s the free API on https://apisetu.gov.in/public/api/cowin''')
    update.message.reply_text('''
Check out the source code on ::
 https://github.com/arv-sajeev
''') 
    return cleanup()




############################################################## Main function 



def main() -> None:
    """Start the bot."""
    # Create the Updater and pass it your bot's token. XXXTOKEN
    updater = Updater("XXXPASTE_YOUR_TOKEN_HEREXXX",use_context=True)


    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher
    
    # Creating a conversation handler to split up the functionalities as states in a conversation
    conv_handler = ConversationHandler(
        entry_points    =   [   CommandHandler('start',start),
                                CommandHandler('bydistrict',find_calendar_bydistrict),
                                CommandHandler('bypincode',find_calendar_bypincode),
                                CommandHandler('cancel',cleanup),
                                CommandHandler('exit',cleanup),
                                CommandHandler('about',about)
                            ],
                            
        states          =   {
                                CHOOSE_QUERY_METHOD :   [
                                                            MessageHandler(Filters.regex('^(District)$'),choose_state),
                                                            MessageHandler(Filters.regex('^PIN-code$'),enter_pincode),
                                                            CommandHandler('cancel',cleanup),
                                                            CommandHandler('exit',cleanup)
                                                        ],
                                CHOOSE_STATE        :   [
                                                            MessageHandler(~Filters.command,choose_district),
                                                            CommandHandler('cancel',cleanup),
                                                            CommandHandler('exit',cleanup),
                                                        ],
                                CHOOSE_DISTRICT     :   [
                                                            MessageHandler(~Filters.command,find_calendar_bydistrict),
                                                            CommandHandler('cancel',cleanup),
                                                            CommandHandler('exit',cleanup)
                                                        ],
                                CHOOSE_PIN          :   [
                                                            MessageHandler(~Filters.command,find_calendar_bypincode),
                                                            CommandHandler('cancel',cleanup),
                                                            CommandHandler('exit',cleanup)
                                                        ],
                                HELP                :   [
                                                            MessageHandler(~Filters.command,cleanup),
                                                            CommandHandler('exit',cleanup),
                                                            CommandHandler('cancel',cleanup)
                                                        ]
                            },
        fallbacks=[CommandHandler('cancel',cleanup)],
    )

    #  Adding the conversation handler to the Dispatcher
    dispatcher.add_handler(conv_handler)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()




if __name__ == '__main__':
    main()


