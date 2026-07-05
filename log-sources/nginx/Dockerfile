FROM nginx:1.27-alpine

# Ukloni podrazumevanu Nginx konfiguraciju
RUN rm /etc/nginx/conf.d/default.conf

# Ugasi podrazumevani access_log u glavnom nginx.conf, da zahteve loguje
# samo naš siem_combined format iz conf.d/default.conf
RUN sed -i 's|access_log\s*/var/log/nginx/access.log\s*main;|# &|' /etc/nginx/nginx.conf

# Nginx slika po defaultu symlinkuje access.log -> /dev/stdout i
# error.log -> /dev/stderr. Menjamo ih pravim fajlovima da bi SIEM
# Ingestor mogao da ih tail-uje iz deljenog volume-a.
RUN rm -f /var/log/nginx/access.log /var/log/nginx/error.log \
    && touch /var/log/nginx/access.log /var/log/nginx/error.log \
    && chown -R nginx:nginx /var/log/nginx

# Ubaci našu konfiguraciju
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]