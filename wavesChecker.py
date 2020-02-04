import os
import sqlite3 as sqlite
import requests
import datetime
import time
import base58
import PyCWaves
import traceback

class WavesChecker(object):
    def __init__(self, config):
        self.config = config
        self.dbCon = sqlite.connect('gateway.db')

        self.node = self.config['waves']['node']
        self.pwTN = PyCWaves.PyCWaves()
        self.pwTN.setNode(node=self.config['tn']['node'], chain=self.config['tn']['network'], chain_id='L')
        seed = os.getenv(self.config['tn']['seedenvname'], self.config['tn']['gatewaySeed'])
        self.tnAddress = self.pwTN.Address(seed=seed)
        self.tnAsset = self.pwTN.Asset(self.config['tn']['assetId'])
        self.pwW = PyCWaves.PyCWaves()
        self.pwW.setNode(node=self.node, chain=self.config['waves']['network'])
        self.wAddress = self.pwW.Address(seed=os.getenv(self.config['waves']['seedenvname'], self.config['waves']['gatewaySeed']))
        self.wAsset = self.pwW.Asset(self.config['waves']['assetId'])

        cursor = self.dbCon.cursor()
        self.lastScannedBlock = cursor.execute('SELECT height FROM heights WHERE chain = "Waves"').fetchall()[0][0]

    def getCurrentBlock(self):
        #return current block on the chain - try/except in case of timeouts
        try:
            CurrentBlock = requests.get(self.node + '/blocks/height').json()['height'] - 1
        except:
            CurrentBlock = 0

        return CurrentBlock

    def run(self):
        #main routine to run continuesly
        print('started checking tn blocks at: ' + str(self.lastScannedBlock))

        self.dbCon = sqlite.connect('gateway.db')
        while True:
            try:
                nextblock = self.getCurrentBlock() - self.config['waves']['confirmations']

                if nextblock > self.lastScannedBlock:
                    self.lastScannedBlock += 1
                    self.checkBlock(self.lastScannedBlock)
                    cursor = self.dbCon.cursor()
                    cursor.execute('UPDATE heights SET "height" = ' + str(self.lastScannedBlock) + ' WHERE "chain" = "Waves"')
                    self.dbCon.commit()
            except Exception as e:
                self.lastScannedBlock -= 1
                print('Something went wrong during waves block iteration: ')
                print(traceback.TracebackException.from_exception(e))

            time.sleep(self.config['tn']['timeInBetweenChecks'])

    def getnow(self):
        #return current datetime in str format
        dateTimeObj = datetime.datetime.now()
        timestampStr = dateTimeObj.strftime("%d-%b-%Y (%H:%M:%S.%f)")
        return timestampStr

    def checkBlock(self, heightToCheck):
        #check content of the block for valid transactions
        block =  requests.get(self.node + '/blocks/at/' + str(heightToCheck)).json()
        for transaction in block['transactions']:
            if self.checkTx(transaction):
                targetAddress = base58.b58decode(transaction['attachment']).decode()

                amount = transaction['amount'] / pow(10, self.config['waves']['decimals'])
                amount -= self.config['tn']['fee']
                amount *= pow(10, self.config['tn']['decimals'])
                amount = int(amount)

                try:
                    addr = self.pwTN.Address(targetAddress)
                    if self.config['tn']['assetId'] == 'TN':
                        tx = self.tnAddress.sendWaves(addr, amount, 'Thanks for using our service!', txFee=2000000)
                    else:
                        tx = self.tnAddress.sendAsset(addr, self.tnAsset, amount, 'Thanks for using our service!', txFee=2000000)

                    if 'error' in tx:
                        self.faultHandler(transaction, "senderror", e=tx['message'])
                    else:
                        print("send tx: " + str(tx))

                        timestampStr = self.getnow()
                        cursor = self.dbCon.cursor()
                        cursor.execute('INSERT INTO executed ("sourceAddress", "targetAddress", "wavesTxId", "tnTxId", "timestamp", "amount", "amountFee") VALUES ("' + transaction['sender'] + '", "' + targetAddress + '", "' + transaction['id'] + '", "' + tx['id'] + '", "' + timestampStr +  '", "' + str(amount) + '", "' + str(self.config['tn']['fee']) + '")')
                        self.dbCon.commit()
                        print('send tokens from waves to tn!')
                except Exception as e:
                    self.faultHandler(transaction, "txerror", e=e)

    def checkTx(self, tx):
        #check the transaction
        if tx['type'] == 4 and tx['recipient'] == self.config['waves']['gatewayAddress'] and tx['assetId'] == self.config['waves']['assetId']:
            #check if there is an attachment
            targetAddress = base58.b58decode(tx['attachment']).decode()
            if len(targetAddress) > 1:
                #check if we already processed this tx
                cursor = self.dbCon.cursor()
                result = cursor.execute('SELECT tnTxId FROM executed WHERE wavesTxId = "' + tx['id'] + '"').fetchall()

                if len(result) == 0: return True
            else:
                self.faultHandler(tx, 'noattachment')

        return False
        
    def faultHandler(self, tx, error, e=""):
        #handle transfers to the gateway that have problems
        amount = tx['amount'] / pow(10, self.config['waves']['decimals'])
        timestampStr = self.getnow()

        if error == "noattachment":
            cursor = self.dbCon.cursor()
            cursor.execute('INSERT INTO errors ("sourceAddress", "targetAddress", "tnTxId", "wavesTxId", "timestamp", "amount", "error") VALUES ("' + tx['sender'] + '", "", "", "' + tx['id'] + '", "' + timestampStr +  '", "' + str(amount) + '", "no attachment found on transaction")')
            self.dbCon.commit()
            print(timestampStr + " - Error: no attachment found on transaction from " + tx['sender'] + " - check errors table.")

        if error == "txerror":
            targetAddress = base58.b58decode(tx['attachment']).decode()
            cursor = self.dbCon.cursor()
            cursor.execute('INSERT INTO errors ("sourceAddress", "targetAddress", "tnTxId", "wavesTxId", "timestamp", "amount", "error", "exception") VALUES ("' + tx['sender'] + '", "' + targetAddress + '", "", "' + tx['id'] + '", "' + timestampStr +  '", "' + str(amount) + '", "tx error, possible incorrect address", "' + str(e) + '")')
            self.dbCon.commit()
            print(timestampStr + " - Error: on outgoing transaction for transaction from " + tx['sender'] + " - check errors table.")

        if error == "senderror":
            targetAddress = base58.b58decode(tx['attachment']).decode()
            cursor = self.dbCon.cursor()
            cursor.execute('INSERT INTO errors ("sourceAddress", "targetAddress", "tnTxId", "wavesTxId", "timestamp", "amount", "error", "exception") VALUES ("' + tx['sender'] + '", "' + targetAddress + '", "", "' + tx['id'] + '", "' + timestampStr +  '", "' + str(amount) + '", "tx error, check exception error", "' + str(e) + '")')
            self.dbCon.commit()
            print(timestampStr + " - Error: on outgoing transaction for transaction from " + tx['sender'] + " - check errors table.")
