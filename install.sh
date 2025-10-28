sudo cp fichaxe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fichaxe.service
sudo systemctl start fichaxe.service