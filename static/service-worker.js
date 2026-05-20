self.addEventListener("push", (event) => {
	let data = {};
	try {
		data = event.data ? event.data.json() : {};
	} catch (e) {
		data = { body: event.data ? event.data.text() : "" };
	}

	const number = data.number || "";
	const title = data.title || "☕ 커피 준비 완료!";
	const options = {
		body:
			data.body ||
			(number ? `${number}번 주문이 나왔습니다!` : "주문 번호가 나왔습니다."),
		tag: number ? `cafe701-${number}` : "cafe701-ready",
		renotify: false,
		requireInteraction: true,
		data: {
			url: data.url || "/",
			watchId: data.watchId || null,
			number,
		},
	};

	event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
	event.notification.close();
	const url =
		event.notification.data && event.notification.data.url
			? event.notification.data.url
			: "/";

	event.waitUntil(
		(async () => {
			const allClients = await clients.matchAll({
				type: "window",
				includeUncontrolled: true,
			});
			for (const client of allClients) {
				if ("focus" in client) {
					client.navigate(url);
					return client.focus();
				}
			}
			if (clients.openWindow) return clients.openWindow(url);
		})(),
	);
});
