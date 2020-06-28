// g++ -Wall -g -I . -o test/sim_anim test/sim_anim.cpp -lpaho-mqttpp3 -lpaho-mqtt3as


#include <iostream>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <cstring>
#include <cctype>
#include <thread>
#include <chrono>
#include "mqtt/client.h"

#include "src/animation_manager.h"
#include "src/handlers.h"
#include "src/utils.h"
#include "src/debug_helpers.h"
#include "src/requests.h"

using namespace std;
using namespace std::chrono;

const string SERVER_ADDRESS	{ "tcp://192.168.5.5:1883" };
const string CLIENT_ID		{ "sim-anim-client" };

// --------------------------------------------------------------------------
// Simple function to manually reconect a client.

bool try_reconnect(mqtt::client& cli)
{
	constexpr int N_ATTEMPT = 30;

	for (int i=0; i<N_ATTEMPT && !cli.is_connected(); ++i) {
		try {
			cli.reconnect();
			return true;
		}
		catch (const mqtt::exception&) {
			this_thread::sleep_for(seconds(1));
		}
	}
	return false;
}

/////////////////////////////////////////////////////////////////////////////

#define RGB_ARRAY_SIZE 150
uint8_t rgb_array[RGB_ARRAY_SIZE*3];

AnimationManager anim_mgr((uint8_t*) rgb_array, RGB_ARRAY_SIZE);
handlers_t handlers;
ElementTopics topics;


int main(int argc, char* argv[])
{
	// setup
	memset(rgb_array, 0, RGB_ARRAY_SIZE*3);
	topics.set_chipid("SIM-00000001");
  
	handlers.ptopics = &topics;
  handlers.panim_mgr = &anim_mgr;


	// ***************
	mqtt::connect_options connOpts;
	connOpts.set_keep_alive_interval(20);
	connOpts.set_clean_session(true);

	mqtt::client cli(SERVER_ADDRESS, CLIENT_ID);

	const string TOPIC("elements/SIM-00000001/operate/#");
	//const vector<string> TOPICS { "elements/SIM-00000001/operate/#" };
	//const vector<int> QOS { 0, 1 };

	try {
		cout << "Connecting to the MQTT server..." << flush;
		mqtt::connect_response rsp = cli.connect(connOpts);
		cout << "OK\n" << endl;

		if (!rsp.is_session_present()) {
			std::cout << "Subscribing to topics..." << std::flush;
			cli.subscribe(TOPIC, 1);
			std::cout << "OK" << std::endl;
		}
		else {
			cout << "Session already present. Skipping subscribe." << std::endl;
		}

		// Consume messages

		while (true) {
			auto msg = cli.consume_message();

			if (!msg) {
				if (!cli.is_connected()) {
					cout << "Lost connection. Attempting reconnect" << endl;
					if (try_reconnect(cli)) {
						cli.subscribe(TOPIC, 1);
						cout << "Reconnected" << endl;
						continue;
					}
					else {
						cout << "Reconnect failed." << endl;
					}
				}
				else {
					cout << "An error occurred retrieving messages." << endl;
				}
				break;
			}
			
			string msgstr = msg->to_string();
			cout << msg->get_topic() << ": [" << msgstr.size() << "]: ";
			printf("%s\n", arr2str((uint8_t *)msgstr.c_str(), msgstr.size(), true));
		
			double t_now = get_time_lf();
			handlers.t_now = t_now;
			const char *errstr = nullptr;

			static char topic[160];
			strncpy(topic, msg->get_topic().c_str(), 159);
			static uint8_t payload_msg[160];
			memcpy(payload_msg, msgstr.c_str(), msgstr.size());
			
			unsigned int length = msgstr.size();
  		process_request(topic, payload_msg, length, handlers, &errstr);
  		if (errstr != nullptr) {
    		DPRINTF("error: %s", errstr);
  		} else {
    		DPRINTF("request processed successfully.");
  		}

		}

		// Disconnect

		cout << "\nDisconnecting from the MQTT server..." << flush;
		cli.disconnect();
		cout << "OK" << endl;
	}
	catch (const mqtt::exception& exc) {
		cerr << exc.what() << endl;
		return 1;
	}

 	return 0;
}

