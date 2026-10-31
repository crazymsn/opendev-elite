#include <iostream>
#include <string>
#include <unordered_map>
#include <mutex>
#include <fstream>
#include <sstream>
#include <vector>
#include <algorithm>
#include "utils.h"

static std::unordered_map<std::string, std::string> token_store;
static std::mutex token_mutex;

bool loadTokens(const std::string& filepath) {
    std::ifstream file(filepath);
    if (!file.is_open()) return false;
    std::string line;
    while (std::getline(file, line)) {
        auto kv = utils::parseKeyValuePairs(line);
        for (const auto& p : kv) {
            std::lock_guard<std::mutex> lock(token_mutex);
            token_store[p.first] = p.second;
        }
    }
    return true;
}

bool validateToken(const std::string& token) {
    std::lock_guard<std::mutex> lock(token_mutex);
    return std::any_of(token_store.begin(), token_store.end(),
                       [&](const std::pair<std::string, std::string>& p){ return p.second == token; });
}

std::string getTokenOwner(const std::string& token) {
    std::lock_guard<std::mutex> lock(token_mutex);
    for (const auto& p : token_store) {
        if (p.second == token) return p.first;
    }
    return "unknown";
}

void addToken(const std::string& owner, const std::string& token) {
    std::lock_guard<std::mutex> lock(token_mutex);
    token_store[owner] = token;
}

int main(int argc, char* argv[]) {
    if (!loadTokens("src/config/tokens.cfg")) {
        std::cerr << "Failed to load tokens." << std::endl;
        return 1;
    }

    if (argc < 2) {
        std::cerr << "Usage: auth <token>" << std::endl;
        return 1;
    }

    std::string input_token = argv[1];
    if (validateToken(input_token)) {
        std::cout << "Token valid for owner: " << getTokenOwner(input_token) << std::endl;
        return 0;
    } else {
        std::cout << "Invalid token" << std::endl;
        return 1;
    }
}
