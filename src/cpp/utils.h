#ifndef UTILS_H
#define UTILS_H

#include <string>
#include <vector>
#include <map>

namespace utils {

std::string randomString(int length);
void splitString(const std::string& s, char delimiter, std::vector<std::string>& out);
std::map<std::string, std::string> parseKeyValuePairs(const std::string& s);
bool fileExists(const std::string& filename);
std::string getTimestamp();

} // namespace utils

#endif // UTILS_H
