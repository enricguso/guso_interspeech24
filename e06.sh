while true; do
	python3 generate_data.py --output /home/ubuntu/dataset --workers 20 --cpu 6 
	sleep 5  # Adjust the sleep duration as needed
done
