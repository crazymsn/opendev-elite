#include "utils.h"
#include <fstream>
#include <ctime>
#include <cstdlib>
#include <sstream>

namespace utils {

std::string randomString(int length) {
    const char charset[] =
        "0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz";
    std::string result;
    result.reserve(length);
    for (int i = 0; i < length; ++i) {
        result += charset[rand() % (sizeof(charset) - 1)];
    }
    return result;
}

void splitString(const std::string& s, char delimiter, std::vector<std::string>& out) {
    out.clear();
    std::stringstream ss(s);
    std::string item;
    while (getline(ss, item, delimiter)) {
        out.push_back(item);
    }
}

std::map<std::string, std::string> parseKeyValuePairs(const std::string& s) {
    std::map<std::string, std::string> result;
    std::vector<std::string> pairs;
    splitString(s, ';', pairs);
    for (const auto& pair : pairs) {
        size_t pos = pair.find('=');
        if (pos != std::string::npos) {
            std::string key = pair.substr(0, pos);
            std::string val = pair.substr(pos + 1);
            result[key] = val;
        }
    }
    return result;
}

bool fileExists(const std::string& filename) {
    std::ifstream f(filename.c_str());
    return f.good();
}

std::string getTimestamp() {
    time_t now = time(nullptr);
    char buf[20];
    strftime(buf, sizeof(buf), "%Y%m%d_%H%M%S", localtime(&now));
    return std::string(buf);
}

} // namespace utils
