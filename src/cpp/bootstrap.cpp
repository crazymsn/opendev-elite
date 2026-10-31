#include <iostream>
#include <vector>
#include <thread>
#include <mutex>
#include <unordered_map>
#include <string>
#include <fstream>
#include <sstream>
#include <ctime>
#include <algorithm>
#include "utils.h"

std::mutex io_mutex;
std::unordered_map<std::string, std::string> config_map;

void loadConfig(const std::string& filepath) {
    std::ifstream file(filepath);
    if (!file.is_open()) return;
    std::string line;
    while (std::getline(file, line)) {
        auto kv = utils::parseKeyValuePairs(line);
        for (const auto& p : kv) {
            config_map[p.first] = p.second;
        }
    }
    file.close();
}

void complexProcessing(const std::string& key, int id) {
    std::vector<int> numbers;
    for (int i = 1; i <= 100 + (id % 50); ++i) {
        numbers.push_back(i * (id + 1));
    }
    std::reverse(numbers.begin(), numbers.end());
    int sum = 0;
    for (auto n : numbers) {
        sum += (n % (id + 1));
    }
    std::string confValue = config_map.count(key) ? config_map[key] : "undefined";
    {
        std::lock_guard<std::mutex> lock(io_mutex);
        std::cout << "Thread " << id << " processed key: " << key << ", sum=" << sum << ", config=" << confValue << std::endl;
    }
}

int main(int argc, char* argv[]) {
    srand(static_cast<unsigned int>(time(nullptr)));
    loadConfig("src/config/config.yaml");

    std::vector<std::string> keys;
    for (int i = 0; i < 10; ++i) {
        keys.push_back("task_" + utils::randomString(5));
    }

    std::vector<std::thread> workers;
    for (int i = 0; i < 8; ++i) {
        workers.emplace_back(complexProcessing, keys[i % keys.size()], i);
    }

    for (auto& t : workers) {
        if (t.joinable()) t.join();
    }

    std::cout << "Bootstrap finished at " << utils::getTimestamp() << std::endl;
    return 0;
}
