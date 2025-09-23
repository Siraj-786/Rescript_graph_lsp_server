// src/Data.res

// A variant type to model different states
type requestStatus =
  | Loading
  | Success(string) // Success carries a payload (string message)
  | Error(int) // Error carries a payload (integer error code)

// A module that encapsulates a helper function
module Request = {
  let fetchData = (status: requestStatus): string => {
    switch status {
    | Loading => "Data is loading..."
    | Success(msg) => "Request succeeded: " ++ msg
    | Error(code) => "Request failed with code: " ++ string_of_int(code)
    }
  }
}

// An exception for custom error handling
exception NetworkError(string)